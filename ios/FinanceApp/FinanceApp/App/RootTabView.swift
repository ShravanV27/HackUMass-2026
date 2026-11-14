import SwiftUI

struct RootTabView: View {
    var body: some View {
        TabView {
            DashboardView()
                .tabItem {
                    Label("Dashboard", systemImage: "house")
                }
            PredictionsView()
                .tabItem {
                    Label("Predictions", systemImage: "chart.line.uptrend.xyaxis")
                }
        }
    }
}

#Preview {
    RootTabView()
}
