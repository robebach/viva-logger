"""Dash frontend for live JSONL logger files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


DEFAULT_LOG_DIR = Path("VIV-LOGS")
DEFAULT_MAX_RECORDS = 2_000
DEFAULT_REFRESH_MS = 5_000


def default_log_path(log_dir: Path = DEFAULT_LOG_DIR) -> Path:
    """Return today's log path, or the newest JSONL file in ``log_dir``."""

    today = log_dir / f"{dt.date.today().isoformat()}.jsonl"
    if today.exists():
        return today

    candidates = list(log_dir.glob("*.jsonl"))
    if not candidates:
        return today

    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_jsonl_records(
    path: Path,
    *,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> list[dict[str, Any]]:
    """Read up to ``max_records`` JSONL records, skipping partial/bad lines."""

    if not path.exists() or not path.is_file():
        return []

    lines: deque[str] = deque(maxlen=max_records)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                lines.append(line)

    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(value, dict):
            records.append(value)

    return records


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten one log record into columns suitable for plotting."""

    row: dict[str, Any] = {
        "timestamp": record.get("timestamp"),
        "device:device_type": record.get("device", {}).get("device_type"),
        "device:serial_number": record.get("device", {}).get("serial_number"),
        "device:connected": record.get("device", {}).get("connected"),
        "errors:count": len(record.get("errors") or []),
    }

    for sensor in record.get("sensors") or []:
        name = sensor.get("name", "sensor")
        row[f"sensor:{name}:status"] = sensor.get("status")
        row[f"sensor:{name}:sensor_type"] = sensor.get("sensor_type")
        _flatten_values(f"sensor:{name}", sensor.get("values") or {}, row)

    for control in record.get("controls") or []:
        name = control.get("name", "control")
        row[f"control:{name}:control_type"] = control.get("control_type")
        _flatten_values(f"control:{name}", control.get("values") or {}, row)

    return row


def records_to_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Convert log records into a time-sorted DataFrame."""

    rows = [flatten_record(record) for record in records]
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"],
        utc=True,
        errors="coerce",
    )
    frame = frame.dropna(subset=["timestamp"])
    return frame.sort_values("timestamp").reset_index(drop=True)


def numeric_columns(frame: pd.DataFrame) -> list[str]:
    """Return columns that contain at least one numeric value."""

    columns: list[str] = []
    for column in frame.columns:
        if column == "timestamp":
            continue

        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().any():
            columns.append(column)

    return columns


def state_columns(frame: pd.DataFrame) -> list[str]:
    """Return non-numeric columns useful for categorical state timelines."""

    columns: list[str] = []
    numeric = set(numeric_columns(frame))
    for column in frame.columns:
        if column == "timestamp":
            continue

        if column in numeric:
            continue

        values = frame[column].dropna()
        if not values.empty and values.nunique() <= 20:
            columns.append(column)

    return columns


def column_label(column: str) -> str:
    """Return a compact human label for a flattened column name."""

    parts = column.split(":")
    if len(parts) < 3:
        return column.replace(":", " ")

    kind, name, *rest = parts
    value = ".".join(rest)
    if kind == "sensor":
        return f"{name} {value}"
    if kind == "control":
        return f"{name} {value}"

    return column.replace(":", " ")


def default_numeric_selection(columns: list[str]) -> list[str]:
    """Choose useful default numeric series for the trends tab."""

    priority = (
        "sensor:temp-rh:rh_pct",
        "sensor:temp-rh:temp_f",
        "sensor:temp-rh:vpd_kpa",
        "control:penelope-mister:target_rh_pct",
        "control:penelope-mister:trigger_below_rh_pct",
        "control:led-0-10v:output_duty_cycle",
        "control:misting-pump-eio4:onoff",
        "errors:count",
    )
    selected = [
        column for column in priority if column in columns
    ]

    preferred_suffixes = (
        ":rh_pct",
        ":temp_f",
        ":vpd_kpa",
        ":output_duty_cycle",
        ":onoff",
        ":relay_state",
        ":target_rh_pct",
        ":trigger_below_rh_pct",
        "errors:count",
    )
    selected.extend(
        column
        for column in columns
        if column.endswith(preferred_suffixes) and column not in selected
    )
    return selected[:8] or columns[:6]


def default_state_selection(columns: list[str]) -> list[str]:
    """Choose useful default categorical series for the state timeline."""

    priority = (
        "control:penelope-mister:mode",
        "control:penelope-mister:phase",
        "control:penelope-mister:active_schedule",
        "control:led-0-10v:schedule:mode",
        "control:led-0-10v:schedule:active_schedule",
        "control:led-0-10v:schedule:selected_schedule",
    )
    selected = [
        column for column in priority if column in columns
    ]

    preferred_suffixes = (
        ":mode",
        ":phase",
        ":active_schedule",
        ":selected_schedule",
    )
    selected.extend(
        column
        for column in columns
        if column.endswith(preferred_suffixes) and column not in selected
    )
    return selected[:6] or columns[:4]


def _flatten_values(prefix: str, values: dict[str, Any], row: dict[str, Any]) -> None:
    """Recursively flatten scalar values from a nested mapping."""

    for key, value in values.items():
        column = f"{prefix}:{key}"
        if isinstance(value, dict):
            _flatten_values(column, value, row)
            continue

        if isinstance(value, (list, tuple, set)):
            continue

        row[column] = value


def create_app(
    *,
    log_path: Optional[Path] = None,
    max_records: int = DEFAULT_MAX_RECORDS,
    refresh_ms: int = DEFAULT_REFRESH_MS,
):
    """Create the Dash app.

    Dash and Plotly are imported lazily so parsing helpers remain usable in
    environments that have not installed the dashboard dependencies yet.
    """

    try:
        from dash import Dash, Input, Output, State, dcc, html
        import plotly.graph_objects as go
    except ModuleNotFoundError as exc:
        missing = exc.name or "dash"
        raise RuntimeError(
            f"Missing dashboard dependency {missing!r}. "
            "Run `poetry install` after adding Dash, or install dash/plotly "
            "in the active environment."
        ) from exc

    log_path = log_path or default_log_path()

    colors = {
        "page": "#f7f8fb",
        "panel": "#ffffff",
        "border": "#d6dbe6",
        "text": "#202939",
        "muted": "#667085",
        "accent": "#1f6feb",
        "good": "#087443",
        "warn": "#b54708",
    }
    page_style = {
        "minHeight": "100vh",
        "backgroundColor": colors["page"],
        "color": colors["text"],
        "fontFamily": "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
        "padding": "22px",
    }
    panel_style = {
        "backgroundColor": colors["panel"],
        "border": f"1px solid {colors['border']}",
        "borderRadius": "8px",
        "padding": "16px",
    }
    card_style = {
        **panel_style,
        "minHeight": "88px",
        "display": "flex",
        "flexDirection": "column",
        "justifyContent": "space-between",
    }

    app = Dash(__name__, title="Viva Logger")

    app.layout = html.Div(
        style=page_style,
        children=[
            dcc.Interval(
                id="refresh",
                interval=refresh_ms,
                n_intervals=0,
            ),
            html.Div(
                style={
                    "display": "flex",
                    "justifyContent": "space-between",
                    "gap": "16px",
                    "alignItems": "flex-end",
                    "marginBottom": "16px",
                    "flexWrap": "wrap",
                },
                children=[
                    html.Div([
                        html.H1(
                            "Viva Logger",
                            style={
                                "fontSize": "28px",
                                "lineHeight": "34px",
                                "margin": "0 0 4px 0",
                            },
                        ),
                        html.Div(
                            id="status-line",
                            style={
                                "color": colors["muted"],
                                "fontSize": "14px",
                            },
                        ),
                    ]),
                    html.Div(
                        style={
                            "display": "grid",
                            "gridTemplateColumns": "minmax(280px, 560px)",
                            "gap": "6px",
                        },
                        children=[
                            html.Label(
                                "Log file",
                                htmlFor="log-path",
                                style={
                                    "fontSize": "12px",
                                    "fontWeight": 700,
                                    "color": colors["muted"],
                                },
                            ),
                            dcc.Input(
                                id="log-path",
                                type="text",
                                value=str(log_path),
                                debounce=True,
                                style={
                                    "height": "36px",
                                    "border": f"1px solid {colors['border']}",
                                    "borderRadius": "6px",
                                    "padding": "0 10px",
                                    "fontSize": "14px",
                                    "minWidth": "280px",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            dcc.Tabs(
                value="readout",
                children=[
                    dcc.Tab(
                        label="Readout",
                        value="readout",
                        children=[
                            html.Div(
                                id="readout",
                                style={"marginTop": "16px"},
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Trends",
                        value="trends",
                        children=[
                            html.Div(
                                style={
                                    "display": "grid",
                                    "gridTemplateColumns": "1fr 1fr",
                                    "gap": "14px",
                                    "marginTop": "16px",
                                },
                                children=[
                                    html.Div(
                                        style=panel_style,
                                        children=[
                                            html.Label(
                                                "Values",
                                                style={
                                                    "fontSize": "12px",
                                                    "fontWeight": 700,
                                                    "color": colors["muted"],
                                                },
                                            ),
                                            dcc.Dropdown(
                                                id="numeric-series",
                                                multi=True,
                                                placeholder="Select values",
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        style=panel_style,
                                        children=[
                                            html.Label(
                                                "States",
                                                style={
                                                    "fontSize": "12px",
                                                    "fontWeight": 700,
                                                    "color": colors["muted"],
                                                },
                                            ),
                                            dcc.Dropdown(
                                                id="state-series",
                                                multi=True,
                                                placeholder="Select states",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                style={
                                    "display": "grid",
                                    "gridTemplateRows": "460px 300px",
                                    "gap": "14px",
                                    "marginTop": "14px",
                                },
                                children=[
                                    html.Div(
                                        style=panel_style,
                                        children=dcc.Graph(
                                            id="numeric-graph",
                                            style={"height": "420px"},
                                        ),
                                    ),
                                    html.Div(
                                        style=panel_style,
                                        children=dcc.Graph(
                                            id="state-graph",
                                            style={"height": "260px"},
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    def metric_card(title: str, value: Any, detail: Optional[str] = None):
        display_value = "n/a" if value is None else value
        return html.Div(
            style=card_style,
            children=[
                html.Div(
                    title,
                    style={
                        "fontSize": "12px",
                        "fontWeight": 700,
                        "color": colors["muted"],
                    },
                ),
                html.Div(
                    display_value,
                    style={
                        "fontSize": "24px",
                        "lineHeight": "30px",
                        "fontWeight": 750,
                    },
                ),
                html.Div(
                    detail or "",
                    style={
                        "fontSize": "12px",
                        "color": colors["muted"],
                        "minHeight": "16px",
                    },
                ),
            ],
        )

    def latest_value(
        frame: pd.DataFrame,
        suffix: str,
        *,
        preferred_prefix: Optional[str] = None,
    ) -> Any:
        matches = [column for column in frame.columns if column.endswith(suffix)]
        if preferred_prefix is not None:
            matches = [
                column for column in matches if column.startswith(preferred_prefix)
            ] or matches

        if not matches or frame.empty:
            return None

        value = frame.iloc[-1][matches[0]]
        return None if pd.isna(value) else value

    def format_number(value: Any, digits: int = 1) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "n/a"
        return f"{number:.{digits}f}"

    def build_readout(records: list[dict[str, Any]], frame: pd.DataFrame):
        if frame.empty or not records:
            return html.Div(
                "No records loaded.",
                style=panel_style,
            )

        latest = frame.iloc[-1]
        timestamp = latest["timestamp"]
        errors_count = int(latest.get("errors:count", 0) or 0)
        connected = latest.get("device:connected")
        serial = latest.get("device:serial_number")

        rh = latest_value(frame, ":rh_pct")
        temp_f = latest_value(
            frame,
            ":temp_f",
            preferred_prefix="sensor:temp-rh:",
        )
        vpd = latest_value(frame, ":vpd_kpa")
        led = latest_value(frame, ":output_duty_cycle")
        target = latest_value(frame, ":target_rh_pct")
        trigger = latest_value(frame, ":trigger_below_rh_pct")
        mister_mode = latest_value(
            frame,
            ":mode",
            preferred_prefix="control:penelope-mister:",
        )
        mister_phase = latest_value(
            frame,
            ":phase",
            preferred_prefix="control:penelope-mister:",
        )
        relay = latest_value(frame, ":onoff")

        cards = html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))",
                "gap": "12px",
            },
            children=[
                metric_card(
                    "Last sample",
                    timestamp.strftime("%H:%M:%S"),
                    timestamp.strftime("%Y-%m-%d UTC"),
                ),
                metric_card(
                    "Device",
                    "connected" if connected else "offline",
                    f"serial {serial}" if serial else None,
                ),
                metric_card("RH", f"{format_number(rh)}%", "temp-rh"),
                metric_card("Air temp", f"{format_number(temp_f)} F", "temp-rh"),
                metric_card("VPD", f"{format_number(vpd, 2)} kPa", "temp-rh"),
                metric_card("LED", format_number(led, 2), "output duty"),
                metric_card(
                    "Mister",
                    mister_mode,
                    f"phase {mister_phase}, relay {relay}",
                ),
                metric_card(
                    "Humidity target",
                    f"{format_number(target)}%",
                    f"trigger {format_number(trigger)}%",
                ),
                metric_card(
                    "Errors",
                    errors_count,
                    "clear" if errors_count == 0 else "check record",
                ),
            ],
        )

        latest_record = records[-1]
        sensor_rows = [
            html.Tr([
                html.Td(sensor.get("name")),
                html.Td(sensor.get("sensor_type")),
                html.Td(sensor.get("status")),
                html.Td(_compact_values(sensor.get("values") or {})),
            ])
            for sensor in latest_record.get("sensors") or []
        ]
        control_rows = [
            html.Tr([
                html.Td(control.get("name")),
                html.Td(control.get("control_type")),
                html.Td(_compact_values(control.get("values") or {})),
            ])
            for control in latest_record.get("controls") or []
        ]

        table_style = {
            "width": "100%",
            "borderCollapse": "collapse",
            "fontSize": "13px",
        }
        cell_style = {
            "borderBottom": f"1px solid {colors['border']}",
            "padding": "8px",
            "verticalAlign": "top",
        }

        return html.Div(
            style={"display": "grid", "gap": "14px"},
            children=[
                cards,
                html.Div(
                    style={
                        "display": "grid",
                        "gridTemplateColumns": "1fr 1fr",
                        "gap": "14px",
                    },
                    children=[
                        html.Div(
                            style=panel_style,
                            children=[
                                html.H2(
                                    "Sensors",
                                    style={"fontSize": "18px", "margin": "0 0 8px 0"},
                                ),
                                html.Table(
                                    style=table_style,
                                    children=[
                                        html.Thead(html.Tr([
                                            html.Th("Name", style=cell_style),
                                            html.Th("Type", style=cell_style),
                                            html.Th("Status", style=cell_style),
                                            html.Th("Values", style=cell_style),
                                        ])),
                                        html.Tbody(sensor_rows),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            style=panel_style,
                            children=[
                                html.H2(
                                    "Controls",
                                    style={"fontSize": "18px", "margin": "0 0 8px 0"},
                                ),
                                html.Table(
                                    style=table_style,
                                    children=[
                                        html.Thead(html.Tr([
                                            html.Th("Name", style=cell_style),
                                            html.Th("Type", style=cell_style),
                                            html.Th("Values", style=cell_style),
                                        ])),
                                        html.Tbody(control_rows),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def empty_figure(title: str):
        figure = go.Figure()
        figure.update_layout(
            title=title,
            template="plotly_white",
            margin={"l": 40, "r": 24, "t": 48, "b": 42},
        )
        return figure

    def numeric_figure(frame: pd.DataFrame, selected: list[str]):
        if frame.empty or not selected:
            return empty_figure("Values")

        figure = go.Figure()
        for column in selected:
            if column not in frame:
                continue

            line_shape = "hv" if column.endswith((":onoff", ":relay_state")) else "linear"
            figure.add_trace(go.Scatter(
                x=frame["timestamp"],
                y=pd.to_numeric(frame[column], errors="coerce"),
                mode="lines",
                name=column_label(column),
                line_shape=line_shape,
                hovertemplate="%{x}<br>%{y}<extra></extra>",
            ))

        figure.update_layout(
            title="Values",
            template="plotly_white",
            hovermode="x unified",
            legend={"orientation": "h", "y": -0.22},
            margin={"l": 48, "r": 24, "t": 48, "b": 72},
            uirevision="viva-values",
        )
        figure.update_xaxes(title=None)
        return figure

    def state_figure(frame: pd.DataFrame, selected: list[str]):
        if frame.empty or not selected:
            return empty_figure("States")

        figure = go.Figure()
        for column in selected:
            if column not in frame:
                continue

            values = frame[column].astype("string").fillna("n/a")
            figure.add_trace(go.Scatter(
                x=frame["timestamp"],
                y=[column_label(column)] * len(frame),
                mode="markers",
                name=column_label(column),
                text=values,
                marker={"size": 10},
                hovertemplate="%{x}<br>%{y}: %{text}<extra></extra>",
            ))

        figure.update_layout(
            title="States",
            template="plotly_white",
            showlegend=False,
            margin={"l": 170, "r": 24, "t": 48, "b": 42},
            uirevision="viva-states",
        )
        figure.update_xaxes(title=None)
        figure.update_yaxes(type="category")
        return figure

    @app.callback(
        Output("readout", "children"),
        Output("numeric-series", "options"),
        Output("numeric-series", "value"),
        Output("state-series", "options"),
        Output("state-series", "value"),
        Output("numeric-graph", "figure"),
        Output("state-graph", "figure"),
        Output("status-line", "children"),
        Input("refresh", "n_intervals"),
        State("log-path", "value"),
        State("numeric-series", "value"),
        State("state-series", "value"),
    )
    def refresh_dashboard(
        _n_intervals: int,
        log_path_value: str,
        selected_numeric: Optional[list[str]],
        selected_state: Optional[list[str]],
    ):
        current_path = Path(log_path_value or "")
        records = read_jsonl_records(current_path, max_records=max_records)
        frame = records_to_frame(records)

        numeric = numeric_columns(frame) if not frame.empty else []
        states = state_columns(frame) if not frame.empty else []
        numeric_options = [
            {"label": column_label(column), "value": column}
            for column in numeric
        ]
        state_options = [
            {"label": column_label(column), "value": column}
            for column in states
        ]

        selected_numeric = [
            column for column in (selected_numeric or []) if column in numeric
        ] or default_numeric_selection(numeric)
        selected_state = [
            column for column in (selected_state or []) if column in states
        ] or default_state_selection(states)

        if frame.empty:
            status = f"{current_path} | no records"
        else:
            latest = frame.iloc[-1]
            status = (
                f"{current_path} | {len(frame)} records | "
                f"newest {latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                f"errors {int(latest.get('errors:count', 0) or 0)}"
            )

        return (
            build_readout(records, frame),
            numeric_options,
            selected_numeric,
            state_options,
            selected_state,
            numeric_figure(frame, selected_numeric),
            state_figure(frame, selected_state),
            status,
        )

    return app


def _compact_values(values: dict[str, Any]) -> str:
    """Return a compact comma-separated representation of scalar values."""

    parts: list[str] = []
    for key, value in values.items():
        if isinstance(value, dict):
            if key == "schedule":
                mode = value.get("mode")
                active = value.get("active_schedule")
                parts.append(f"{key}={mode or active}")
            elif key == "last_event":
                event = value.get("event")
                reason = value.get("stop_reason")
                parts.append(f"{key}={event or reason}")
            continue

        if isinstance(value, float):
            parts.append(f"{key}={value:.3f}")
        elif not isinstance(value, (list, tuple, set)):
            parts.append(f"{key}={value}")

    return ", ".join(parts[:10])


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the dashboard server."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=default_log_path(),
        help="JSONL log file to display.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the Dash server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8050,
        help="Port for the Dash server.",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=DEFAULT_MAX_RECORDS,
        help="Maximum recent JSONL records to load on each refresh.",
    )
    parser.add_argument(
        "--refresh-ms",
        type=int,
        default=DEFAULT_REFRESH_MS,
        help="Browser refresh interval in milliseconds.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Dash in debug mode.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """Run the dashboard server from the command line."""

    args = parse_args(argv)
    app = create_app(
        log_path=args.log,
        max_records=args.max_records,
        refresh_ms=args.refresh_ms,
    )
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
