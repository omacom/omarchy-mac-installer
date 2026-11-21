import SwiftUI

struct OmarchyResumePage: View {
  let onBusyChanged: (Bool) -> Void
  let onClose: () -> Void
  let encrypt: Bool
  let lane: String

  var body: some View {
    ResumePageFrame {
      OmarchyResumeView(
        onBusyChanged: onBusyChanged, onClose: onClose, encrypt: encrypt, lane: lane)
    }
  }
}

struct ResumePageFrame<Content: View>: View {
  @ViewBuilder let content: () -> Content

  var body: some View {
    VStack(spacing: 16) {
      OmarchyWordmark().frame(maxWidth: 400).padding(.top, 24)
      content()
        .background(OmarchyTheme.card, in: RoundedRectangle(cornerRadius: OmarchyTheme.cardRadius))
      Spacer(minLength: 24)
    }
    .padding(.horizontal, 40)
    .frame(maxWidth: .infinity, minHeight: OnePageInstallerView.minimumContentHeight)
    .foregroundStyle(OmarchyTheme.text)
    .background(OmarchyTheme.window)
  }
}
