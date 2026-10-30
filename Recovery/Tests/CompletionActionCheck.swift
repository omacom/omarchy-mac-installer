import AppKit

@main struct Check {
  static func main() {
    func action(_ modifiers: NSEvent.ModifierFlags, available: Bool = true) -> CompletionAction {
      CompletionAction.current(modifiers: modifiers, accountAvailable: available)
    }
    precondition(action([]) == .restart)
    precondition(action(.shift) == .createRecoveryAccount)
    precondition(action([.shift, .capsLock]) == .createRecoveryAccount, "Caps Lock blocked Shift")
    precondition(action([.shift, .function]) == .createRecoveryAccount, "Fn blocked Shift")
    precondition(action(.capsLock) == .restart, "Caps Lock alone offered the account")
    for other: NSEvent.ModifierFlags in [.command, .option, .control] {
      precondition(action(other) == .restart, "\(other.rawValue) alone offered the account")
      precondition(
        action([.shift, other]) == .restart, "Shift-\(other.rawValue) offered the account")
    }
    // No account to offer: a started, finalized or not-required account, or a restart under way.
    precondition(action(.shift, available: false) == .restart)
    precondition(action([], available: false) == .restart)
    precondition(CompletionAction.restart.title == "Restart")
    precondition(CompletionAction.createRecoveryAccount.title == "Create Recovery Account")
    print("PASS completion action: Shift alone turns Restart into Create Recovery Account")
  }
}
