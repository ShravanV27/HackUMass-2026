import SwiftUI

struct MetricInfoButton: View {
    let title: String
    let explanation: String

    @State private var showInfo = false

    var body: some View {
        Button {
            showInfo = true
        } label: {
            Image(systemName: "info.circle")
                .foregroundStyle(.secondary)
                .accessibilityLabel("About \(title)")
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showInfo) {
            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.headline)
                Text(explanation)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding()
            .frame(maxWidth: 280, alignment: .leading)
            .presentationCompactAdaptation(.popover)
        }
    }
}

enum MetricGlossary {
    static let safeToSpend =
        "Cash you can spend after pending items, known bills, typical daily spending, and your safety buffer. Credit cards are excluded. Future paychecks only count when you set a payment cycle in Settings."
    static let overdraftRisk =
        "On track = stays above your buffer. Watch closely = dips below the buffer. At risk = may go below zero. Suggested cutbacks come from the forecast engine, not AI."
    static let spendingDrift =
        "Compares recent spending to your usual pace over a longer window. Transfers and pending items are excluded."
    static let estimatedSpend =
        "Typical discretionary spending by weekday, based on your history — not counting categories already covered by known bills."
    static let aiInsight =
        "AI text is labeled when shown. It only narrates your forecast numbers — it never invents balances or moves money."
    static let forecastAssumptions =
        "Forecasts are estimates from recent spending, known bills, and any paydays you configured. They are guidance, not a guarantee."
}

#Preview {
    MetricInfoButton(title: "Safe to spend", explanation: MetricGlossary.safeToSpend)
}
