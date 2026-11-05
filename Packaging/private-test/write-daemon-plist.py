#!/usr/bin/env python3
"""Write the LaunchDaemon plist the private tester package installs.

The client requirement is always the built app's exact cdhash. A Neo hardware
package also passes the decoder requirement and the recovery-template cdhash,
derived after the app is signed. Omitting them leaves the private M3 and
Limine plists unchanged.
"""
import plistlib
import sys


def main():
    if len(sys.argv) not in (4, 6):
        raise SystemExit(
            "usage: write-daemon-plist.py PLIST CLIENT_REQUIREMENT LABEL"
            " [RESTORE_IMAGE_REQUIREMENT RECOVERY_TEMPLATE_REQUIREMENT]"
        )
    plist_path, client, label = sys.argv[1:4]
    environment = {"OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT": client}
    if len(sys.argv) == 6:
        environment["OMARCHY_RESTORE_IMAGE_REQUIREMENT"] = sys.argv[4]
        environment["OMARCHY_RECOVERY_TEMPLATE_REQUIREMENT"] = sys.argv[5]
    with open(plist_path, "wb") as output:
        plistlib.dump(
            {
                "Label": label,
                "Program": "/Library/PrivilegedHelperTools/" + label,
                "MachServices": {label: True},
                "UserName": "root",
                "EnvironmentVariables": environment,
            },
            output,
        )


if __name__ == "__main__":
    main()
