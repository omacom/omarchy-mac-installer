import OmarchyInstallerUXCore
import SwiftUI

/// The app's own confirmation: roomier than the system dialog, with the
/// wording, the buttons, and the space between them under the app's control.
/// Every confirmation in the installer uses this so they all look alike.
struct ConfirmationSheet: View {
  let title: String
  let message: String
  let action: String
  /// Return cancels, for a choice the person should make deliberately.
  var cancelIsDefault = false
  let onConfirm: () -> Void
  let onCancel: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Text(title)
        .font(OmarchyTheme.title)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.bottom, 14)
      Text(message)
        .font(OmarchyTheme.body)
        .foregroundStyle(OmarchyTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.bottom, 34)
      HStack(spacing: 16) {
        Spacer(minLength: 0)
        Button(PlainLanguage.cancel, action: onCancel)
          .buttonStyle(OmarchyButtonStyle(kind: cancelIsDefault ? .primary : .secondary))
          .keyboardShortcut(cancelIsDefault ? .defaultAction : .cancelAction)
        Button(action, action: onConfirm)
          .buttonStyle(OmarchyButtonStyle(kind: cancelIsDefault ? .secondary : .primary))
          .keyboardShortcut(cancelIsDefault ? nil : .defaultAction)
      }
    }
    .onExitCommand(perform: onCancel)
    .padding(32)
    .frame(width: 520)
    .omarchyTypography()
    .foregroundStyle(OmarchyTheme.text)
    .background(OmarchyTheme.window)
  }
}
