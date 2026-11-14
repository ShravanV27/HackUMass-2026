import SwiftUI

struct MoneyRow: View {
    let title: String
    let value: Decimal
    var emphasis: Bool = false

    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Text(value, format: .currency(code: "USD"))
                .fontWeight(emphasis ? .semibold : .regular)
        }
        .font(.subheadline)
    }
}

enum RiskTone {
    static func color(for risk: String) -> Color {
        switch risk.lowercased() {
        case "likely": return .red
        case "watch": return .orange
        default: return .green
        }
    }

    static func label(for risk: String) -> String {
        switch risk.lowercased() {
        case "likely": return "At risk"
        case "watch": return "Watch closely"
        default: return "On track"
        }
    }
}

enum BehaviorLabel {
    static func display(_ segment: String) -> String {
        switch segment.lowercased() {
        case "steady": return "Steady spender"
        case "spiky": return "Spiky spending"
        case "weekend_heavy": return "Weekend-heavy"
        case "bill_stressed": return "Bill-stressed"
        default: return segment.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}
