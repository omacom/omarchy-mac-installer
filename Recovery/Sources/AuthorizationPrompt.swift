import Foundation

/// The password request on the first screen. Installing asks the owner who
/// authorizes the boot policy; repairing the startup disk asks the same owner
/// before shutting down.
struct AuthorizationPrompt: Equatable {
  enum Purpose: Equatable {
    case install, repair
  }

  var purpose: Purpose
  var owner: String
  /// The original macOS owner authorizes: the bootstrap owner in Recovery, or
  /// the current user in the preview. A target owner such as the recovery
  /// account is not a macOS owner.
  var macOSOwner: Bool
  /// Why the last password was refused; shown in place of the request.
  var rejection: String?

  var title: String { purpose == .repair ? "Authorize startup disk" : "Finish setup" }

  var message: String {
    var text =
      rejection.map { $0 + " Enter it again, or choose Utilities → Show Details." }
      ?? "Enter password for \(owner)."
    if purpose == .install && macOSOwner {
      text += " Use your existing macOS owner’s password to set up Omarchy."
    }
    return text
  }

  var action: String { purpose == .repair ? "Shut Down" : "Install" }
}
