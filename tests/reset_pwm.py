from labjack import ljm


def main() -> None:
    handle = ljm.openS("T4", "ANY", "ANY")

    try:
        info = ljm.getHandleInfo(handle)
        print(f"Connected: devType={info[0]}, connType={info[1]}, serial={info[2]}")

        # Disable PWM/extended features on both possible PWM pins.
        # Your real wire is FIO7 / DIO7, but DIO6 was used in the old code.
        for dio in [6, 7]:
            try:
                ljm.eWriteName(handle, f"DIO{dio}_EF_ENABLE", 0)
                print(f"Disabled DIO{dio}_EF_ENABLE")
            except Exception as exc:
                print(f"Could not disable DIO{dio}_EF_ENABLE: {exc}")

        # Disable Clock0 so no PWM clock is running.
        try:
            ljm.eWriteName(handle, "DIO_EF_CLOCK0_ENABLE", 0)
            print("Disabled DIO_EF_CLOCK0_ENABLE")
        except Exception as exc:
            print(f"Could not disable DIO_EF_CLOCK0_ENABLE: {exc}")

        # On T4 flexible I/O, reading DIO4-DIO11 first can force the line
        # into digital-input mode, then writing can make it a digital output.
        # This helps if the pin was previously treated as analog/flex I/O.
        ljm.eReadName(handle, "DIO7")
        ljm.eWriteName(handle, "DIO7", 0)
        print("Forced DIO7 low.")

        print("\nRecovery complete.")

    finally:
        ljm.close(handle)


if __name__ == "__main__":
    main()