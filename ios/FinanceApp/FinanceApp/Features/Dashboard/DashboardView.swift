import SwiftUI

/// Legacy dashboard retained for source compatibility. Phase 4 Home is `HomeView`.
struct DashboardView: View {
    var body: some View {
        HomeView()
    }
}

#Preview {
    DashboardView()
        .environment(AppSession())
}
