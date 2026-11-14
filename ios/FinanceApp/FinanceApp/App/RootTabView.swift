import SwiftUI

struct RootTabView: View {
    var body: some View {
        TabView {
            HomeView()
                .tabItem {
                    Label("Home", systemImage: "house.fill")
                }
            InsightsView()
                .tabItem {
                    Label("Insights", systemImage: "sparkles")
                }
            WhatIfView()
                .tabItem {
                    Label("What If?", systemImage: "arrow.triangle.branch")
                }
            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gearshape.fill")
                }
        }
    }
}

#Preview {
    RootTabView()
        .environment(AppSession())
}
