import AppKit
import OmarchyInstallerUXCore
import SwiftUI

/// An optional preference action, outside installation approval and execution.
struct ProMotionPanel: View {
  @Environment(\.scenePhase) private var scenePhase
  @State private var selection: ProMotionSelection = .hidden
  @State private var failed = false
  let isSimulation: Bool
  let isBusy: Bool

  private var action: ProMotionAction {
    ProMotionAction(access: MacProMotionDisplay(), isSimulation: isSimulation)
  }

  var body: some View {
    Group {
      if selection != .hidden {
        VStack(alignment: .leading, spacing: 8) {
          if selection == .enabled && !failed {
            Text(PlainLanguage.proMotionEnabled)
          } else {
            Text(PlainLanguage.proMotionSuggestion)
            Text(PlainLanguage.proMotionPreference)
              .foregroundStyle(OmarchyTheme.secondaryText)
            if failed {
              Text(PlainLanguage.proMotionFailure)
                .foregroundStyle(OmarchyTheme.caution)
            }
            HStack {
              if case .available = selection {
                Button(PlainLanguage.proMotionEnable) {
                  guard !isBusy, !isSimulation else { return }
                  do {
                    try action.enable()
                    failed = false
                    refresh()
                  } catch {
                    failed = true
                    selection = .manual
                  }
                }
                .omarchySecondaryButton()
              }
              Button(PlainLanguage.proMotionSettings) {
                guard !isBusy, !isSimulation else { return }
                if !NSWorkspace.shared.open(
                  URL(fileURLWithPath: "/System/Library/PreferencePanes/Displays.prefPane"))
                {
                  failed = true
                }
              }
              .omarchySecondaryButton()
            }
            .disabled(isBusy)
          }
        }
        .font(OmarchyTheme.body)
      }
    }
    .task { refresh() }
    .onChange(of: scenePhase) { _, phase in
      if phase == .active && !isBusy { refresh() }
    }
  }

  private func refresh() {
    do {
      selection = try action.inspect()
      if selection == .enabled { failed = false }
    } catch {
      // Inspection must never prevent installation or claim a successful change.
      // Unknown hardware must not receive an unsupported ProMotion suggestion.
      if selection != .hidden { selection = .manual }
    }
  }
}
