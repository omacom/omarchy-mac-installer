import Foundation

@main struct BootSelectionCheck {
  static func main() throws {
    precondition(
      RecoveryBootPolicy.authorizationArguments(groupUUID: "target") == ["-nc", "-v", "target"])
    precondition(
      RecoveryBootPolicy.authorizationArguments(
        groupUUID: "target", allowCustomBootArguments: true) == ["-nca", "-v", "target"])
    let initial = BootSelection()
    precondition(initial.mode == .raw && !initial.nestedVirtualization)
    let volume = "/Volumes/Volume With Spaces"
    for mode in [AuraBootMode.raw, .macho] {
      let selection = BootSelection(mode: mode)
      precondition(!selection.nestedVirtualization)
      let stage1 = "/payloads/" + selection.mode.stage1Path
      let arguments = selection.mode.enrollmentArguments(stage1: stage1, volume: volume)
      precondition(arguments[0] == "configure-boot")
      precondition(arguments[2] == stage1 && arguments[4] == volume)
      precondition(selection.mode.stage2Path == "raw/boot.bin")
      switch mode {
      case .raw:
        precondition(
          Array(arguments.dropFirst(5)) == [
            "--raw", "--entry-point", "2048", "--lowest-virtual-address", "0",
          ])
        precondition(selection.mode.espStage2Path == "m1n1/boot.bin")
        precondition(selection.mode.executeWindowSize == nil)
      case .macho:
        precondition(Array(arguments.dropFirst(5)) == ["--compress"])
        precondition(selection.mode.espStage2Path == "m1n1/boot-ctxr.bin")
        precondition(selection.mode.executeWindowSize == ExecuteWindow.size)
      }
      let restored = try JSONDecoder().decode(
        BootSelection.self, from: JSONEncoder().encode(selection))
      precondition(
        restored.mode == selection.mode
          && restored.nestedVirtualization == selection.nestedVirtualization)
    }
    print(
      "PASS raw enrolls --raw for m1n1/boot.bin; Mach-O enrolls --compress for m1n1/boot-ctxr.bin"
    )
  }
}
