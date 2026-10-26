import OmarchyInstallerUXCore
import SwiftUI

/// Migrate to Native: offers the export of the VM's home, or says how to make
/// one. Laid out like `ConfirmationSheet`, with Install Without Migrating
/// apart on the left.
struct MigrationSheet: View {
  let offer: TrialMigrationOffer
  let onPrimary: () -> Void
  let onInstallWithoutMigrating: () -> Void
  let onCancel: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Text(offer.title)
        .font(OmarchyTheme.title)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.bottom, 14)
      Text(offer.message)
        .font(OmarchyTheme.body)
        .foregroundStyle(OmarchyTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.bottom, 14)
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        if offer.isBusy {
          ProgressView().controlSize(.small)
        }
        if let line = offer.checkLine {
          Text(line)
            .font(OmarchyTheme.body)
            .foregroundStyle(offer.checkFailed ? OmarchyTheme.caution : OmarchyTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      .padding(.bottom, 8)
      if case .export = offer {
        Text(PlainLanguage.tryExportComesAlong)
          .omarchyHelpText()
          .fixedSize(horizontal: false, vertical: true)
      }
      HStack(spacing: 16) {
        Button(PlainLanguage.trialInstallWithoutMigrating, action: onInstallWithoutMigrating)
          .buttonStyle(OmarchyButtonStyle(kind: .secondary))
          .disabled(offer == .waiting(stopping: true))
        Spacer(minLength: 0)
        Button(PlainLanguage.cancel, action: onCancel)
          .buttonStyle(OmarchyButtonStyle(kind: offer.primary == nil ? .primary : .secondary))
          .keyboardShortcut(offer.primary == nil ? .defaultAction : .cancelAction)
        if let primary = offer.primary {
          Button(primary.title, action: onPrimary)
            .buttonStyle(OmarchyButtonStyle(kind: .primary))
            .keyboardShortcut(.defaultAction)
            .disabled(!primary.isEnabled)
        }
      }
      .padding(.top, 26)
    }
    .onExitCommand(perform: onCancel)
    .padding(32)
    .frame(width: 660)
    .omarchyTypography()
    .foregroundStyle(OmarchyTheme.text)
    .background(OmarchyTheme.window)
  }
}
