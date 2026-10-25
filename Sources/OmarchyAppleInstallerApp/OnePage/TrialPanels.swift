import AppKit
import OmarchyInstallerUXCore
import SwiftUI

/// Try's part of the page: the Try page, then Omarchy's state while it runs
/// in its own window.
struct TrialStage: View {
  let trial: TrialSession

  var body: some View {
    switch trial.phase {
    case .hidden:
      EmptyView()

    case .ready(let space, let permissions):
      Panel {
        Text(PlainLanguage.trialHeadline).font(OmarchyTheme.heading)
        Text(trial.subheadline)
          .font(OmarchyTheme.body)
          .foregroundStyle(OmarchyTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
      }
      Panel {
        PermissionRows(trial: trial, permissions: permissions, onlyMissing: false)
        Text(PlainLanguage.trialPermissionsNote)
          .font(OmarchyTheme.detail)
          .foregroundStyle(OmarchyTheme.secondaryText)
      }
      Text(PlainLanguage.trialSpace(space))
        .font(OmarchyTheme.body)
        .foregroundStyle(OmarchyTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
      if !space.isEnough {
        Text(PlainLanguage.trialSpaceShortfall(space))
          .font(OmarchyTheme.body)
          .foregroundStyle(OmarchyTheme.caution)
      }

    case .starting(let progress):
      Panel {
        HStack(alignment: .firstTextBaseline) {
          Text(progress.title).font(OmarchyTheme.heading)
          Spacer(minLength: 8)
          if let percent = progress.percent {
            Text("\(percent)%").font(OmarchyTheme.detail).foregroundStyle(
              OmarchyTheme.secondaryText)
          }
        }
        ProgressTrack(fraction: progress.fraction, height: 14)
      }

    case .running(let permissions):
      Panel {
        Text(PlainLanguage.trialRunningHeadline).font(OmarchyTheme.heading)
        Text(PlainLanguage.trialRunningDetail)
          .font(OmarchyTheme.body)
          .foregroundStyle(OmarchyTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        if trial.finishInstallAfterShutDown {
          Text(PlainLanguage.trialFinishInstall)
            .font(OmarchyTheme.body)
            .foregroundStyle(OmarchyTheme.caution)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      if let line = trial.bridgedNetworkLine {
        Panel {
          HStack {
            Text(line)
              .font(OmarchyTheme.body)
              .foregroundStyle(OmarchyTheme.secondaryText)
              .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            if let action = trial.bridgedNetworkActionTitle {
              Button(action) {
                Task { await trial.setUpBridgedNetwork() }
              }
              .omarchySecondaryButton()
            }
          }
        }
      }
      if !permissions.allAllowed {
        Panel {
          PermissionRows(trial: trial, permissions: permissions, onlyMissing: true)
          Text(PlainLanguage.trialPermissionsNextStart)
            .font(OmarchyTheme.detail)
            .foregroundStyle(OmarchyTheme.secondaryText)
        }
      }

    case .stopping:
      Panel {
        Text(PlainLanguage.trialStoppingHeadline).font(OmarchyTheme.heading)
        Text(PlainLanguage.trialStoppingDetail)
          .font(OmarchyTheme.body)
          .foregroundStyle(OmarchyTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        ProgressTrack(fraction: nil, height: 14)
      }

    case .failed(let failure):
      Panel {
        Text(failure.headline)
          .font(OmarchyTheme.heading)
          .foregroundStyle(OmarchyTheme.danger)
        Text(failure.detail)
          .font(OmarchyTheme.body)
          .foregroundStyle(OmarchyTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        if let technical = failure.technicalDetail {
          DisclosureGroup("Technical details") {
            TechnicalDetailText(text: technical)
            Button("Copy error details") {
              NSPasteboard.general.clearContents()
              NSPasteboard.general.setString(technical, forType: .string)
            }
          }
        }
      }
    }
  }
}

/// The landing page's lines above its buttons: why Install can't go ahead,
/// or that the check still runs, what Try has, and the space for it. With
/// nothing to say, one line names both choices. The space keeps its height
/// in every state.
struct TrialLandingMessage: View {
  let trial: TrialSession
  let canCheckAgain: Bool
  let onCheckAgain: () -> Void
  @State private var details: TrialInstallReason?

  var body: some View {
    let landing = trial.landing
    VStack(spacing: 6) {
      switch landing.install {
      case .checking:
        HStack(spacing: 8) {
          ProgressView().controlSize(.small)
          Text(PlainLanguage.trialCheckingInstall)
            .foregroundStyle(OmarchyTheme.secondaryText)
        }
      case .available:
        EmptyView()
      case .unavailable(let reason):
        HStack(alignment: .firstTextBaseline, spacing: 14) {
          Text(reason.line)
            .foregroundStyle(reason.canCheckAgain ? OmarchyTheme.caution : OmarchyTheme.text)
          HStack(spacing: 12) {
            if reason.canCheckAgain {
              Button(PlainLanguage.checkAgain, action: onCheckAgain)
                .disabled(!canCheckAgain)
            }
            Button(PlainLanguage.trialInstallDetails) { details = reason }
          }
          .buttonStyle(.link)
          .font(OmarchyTheme.detail)
          .foregroundStyle(OmarchyTheme.accent)
          .fixedSize()
        }
      }
      if let line = landing.tryLine ?? landing.choiceLine {
        Text(line)
          .foregroundStyle(OmarchyTheme.secondaryText)
      }
      if let line = landing.spaceLine {
        Text(line)
          .foregroundStyle(landing.spaceIsShort ? OmarchyTheme.caution : OmarchyTheme.secondaryText)
      }
    }
    .font(OmarchyTheme.body)
    .multilineTextAlignment(.center)
    .frame(maxWidth: .infinity, minHeight: 72)
    .alert(details?.headline ?? "", isPresented: detailsShown, presenting: details) { reason in
      if reason.canCheckAgain {
        Button(PlainLanguage.checkAgain) { onCheckAgain() }
      }
      Button("OK", role: .cancel) {}
    } message: { reason in
      Text(reason.detail)
    }
  }

  private var detailsShown: Binding<Bool> {
    Binding(get: { details != nil }, set: { if !$0 { details = nil } })
  }
}

/// The landing page's two buttons, side by side and the same width: Install
/// on this Mac, or Migrate to Native once a VM exists, and Try it in a VM.
struct TrialLandingButtons: View {
  let trial: TrialSession
  let onInstall: () -> Void

  var body: some View {
    let landing = trial.landing
    button(
      landing.installTitle, isPrimary: !landing.tryIsPrimary,
      isEnabled: landing.install == .available
    ) {
      trial.chooseInstall(then: onInstall)
    }
    button(landing.tryTitle, isPrimary: landing.tryIsPrimary, isEnabled: true) {
      trial.present()
    }
  }

  private func button(
    _ title: String, isPrimary: Bool, isEnabled: Bool, action: @escaping () -> Void
  ) -> some View {
    Button(title, action: action)
      .buttonStyle(OmarchyButtonStyle(kind: isPrimary ? .primary : .secondary, fillsWidth: true))
      .frame(width: 240)
      .disabled(!isEnabled)
      .keyboardShortcut(isPrimary && isEnabled ? .defaultAction : nil)
  }
}

/// Try's buttons. Install on this Mac shuts Omarchy down, then continues
/// into the installer's plan. Migrate to Native says first that the VM's
/// files stay where they are.
struct TrialActions: View {
  let trial: TrialSession
  let onInstall: () -> Void

  var body: some View {
    switch trial.phase {
    case .hidden, .starting:
      EmptyView()

    case .ready:
      Button(PlainLanguage.trialBack) { trial.dismiss() }
        .omarchySecondaryButton()
      Button(trial.startTitle) { trial.start() }
        .omarchyPrimaryButton()
        .disabled(!trial.canStart)
        .keyboardShortcut(.defaultAction)

    case .running:
      Button(PlainLanguage.trialShutDown) { trial.shutDown() }
        .omarchySecondaryButton()
      if trial.canInstall {
        Button(trial.installTitle) { trial.chooseInstall(then: onInstall) }
          .omarchyPrimaryButton()
      }

    case .stopping:
      EmptyView()

    case .failed:
      if trial.canTryAgain {
        Button(PlainLanguage.trialBack) { trial.dismiss() }
          .omarchySecondaryButton()
        Button(PlainLanguage.trialTryAgain) { trial.tryAgain() }
          .omarchyPrimaryButton()
          .keyboardShortcut(.defaultAction)
      } else {
        Button(PlainLanguage.trialBack) { trial.dismiss() }
          .omarchySecondaryButton()
          .keyboardShortcut(.defaultAction)
      }
    }
  }
}

private struct PermissionRows: View {
  let trial: TrialSession
  let permissions: TrialPermissions
  /// The running page lists only what is still not granted.
  let onlyMissing: Bool

  var body: some View {
    if !onlyMissing || permissions.accessibility != .allowed {
      PermissionRow(
        title: PlainLanguage.trialAccessibility, state: permissions.accessibility,
        action: { trial.requestAccessibility() })
    }
    if !onlyMissing || permissions.microphone != .allowed {
      PermissionRow(
        title: PlainLanguage.trialMicrophone, state: permissions.microphone,
        action: { Task { await trial.requestMicrophone() } })
    }
    if !onlyMissing || permissions.camera != .allowed {
      PermissionRow(
        title: PlainLanguage.trialCamera, state: permissions.camera,
        action: { Task { await trial.requestCamera() } })
    }
  }
}

private struct PermissionRow: View {
  let title: String
  let state: TrialPermissionState
  let action: () -> Void

  var body: some View {
    HStack {
      Text(title).font(OmarchyTheme.body)
      Spacer(minLength: 8)
      if state == .allowed {
        StatusBadge(text: PlainLanguage.trialAllowed, kind: .ok)
      } else {
        Button(
          state == .refused ? PlainLanguage.trialOpenSettings : PlainLanguage.trialAllow,
          action: action
        )
        .omarchySecondaryButton()
      }
    }
  }
}
