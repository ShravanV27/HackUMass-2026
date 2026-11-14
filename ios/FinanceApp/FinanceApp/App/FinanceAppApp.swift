import SwiftUI

@main
struct FinanceAppApp: App {
    @State private var session = AppSession()

    var body: some Scene {
        WindowGroup {
            Group {
                if session.hasCompletedOnboarding {
                    RootTabView()
                } else {
                    CustomerEntryView(session: session)
                }
            }
            .environment(session)
            .tint(Color(red: 0.12, green: 0.35, blue: 0.38))
        }
    }
}
