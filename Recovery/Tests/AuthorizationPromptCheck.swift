import Foundation

@main struct Check {
  static func main() {
    let install = AuthorizationPrompt(
      purpose: .install, owner: "jane", macOSOwner: true, rejection: nil)
    precondition(install.title == "Finish setup")
    precondition(install.action == "Install")
    precondition(
      install.message
        == "Enter password for jane. Use your existing macOS owner’s password to set up Omarchy.",
      install.message)

    // A target owner, such as the recovery account, is not a macOS owner.
    var target = install
    target.macOSOwner = false
    precondition(target.message == "Enter password for jane.", target.message)

    // A refused password is asked for again in place, naming the menu item.
    let refused = RecoveryPasswordRejected(username: "jane").localizedDescription
    var rejected = install
    rejected.rejection = refused
    precondition(
      rejected.message
        == "The password for jane was not accepted. Enter it again, or choose Utilities → Show Details. Use your existing macOS owner’s password to set up Omarchy.",
      rejected.message)
    target.rejection = refused
    precondition(
      target.message
        == "The password for jane was not accepted. Enter it again, or choose Utilities → Show Details.",
      target.message)

    // Repairing the startup disk shuts down; the setup sentence does not apply.
    var repair = install
    repair.purpose = .repair
    precondition(repair.title == "Authorize startup disk")
    precondition(repair.action == "Shut Down")
    precondition(repair.message == "Enter password for jane.", repair.message)
    repair.rejection = refused
    precondition(
      repair.message
        == "The password for jane was not accepted. Enter it again, or choose Utilities → Show Details.",
      repair.message)
    print(
      "PASS authorization prompt: install, target owner, rejection retry and startup-disk repair copy"
    )
  }
}
