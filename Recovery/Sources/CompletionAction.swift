import AppKit

/// The finished screen's one button. An optional recovery account that was
/// never started stays out of the way: while Shift alone is held, Restart
/// becomes Create Recovery Account, like an Option-modified menu item.
enum CompletionAction: Equatable {
  case restart
  case createRecoveryAccount

  static func current(
    modifiers: NSEvent.ModifierFlags, accountAvailable: Bool
  ) -> CompletionAction {
    // Caps Lock, Fn and the like don't count; Shift with Command, Option or
    // Control is a shortcut in progress, not a request for the account.
    let held = modifiers.intersection([.shift, .control, .option, .command])
    return accountAvailable && held == .shift ? .createRecoveryAccount : .restart
  }

  var title: String {
    switch self {
    case .restart: return "Restart"
    case .createRecoveryAccount: return "Create Recovery Account"
    }
  }
}
