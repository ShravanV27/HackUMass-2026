import SwiftUI

/// Legacy predictions tab — Phase 4 routes forecasting into Home and What If?.
struct PredictionsView: View {
    var body: some View {
        WhatIfView()
    }
}

#Preview {
    PredictionsView()
        .environment(AppSession())
}
