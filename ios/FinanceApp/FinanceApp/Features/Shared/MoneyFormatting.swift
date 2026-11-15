import SwiftUI
import UIKit

/// Money entry on a decimal pad. Not a one-time-code field: iOS screenshots the
/// screen while that content type is focused, looking for a verification code.
struct DecimalAmountField: View {
    let placeholder: String
    @Binding var text: String
    var alignment: TextAlignment = .leading
    var bordered: Bool = false

    var body: some View {
        QuietEntryField(
            placeholder: placeholder,
            text: $text,
            keyboard: .decimalPad,
            alignment: alignment,
            bordered: bordered
        )
    }
}

/// Plain text entry. The AutoFill shortcut bar is removed without using `.oneTimeCode`.
struct QuietEntryField: UIViewRepresentable {
    let placeholder: String
    @Binding var text: String
    var keyboard: UIKeyboardType = .default
    var alignment: TextAlignment = .leading
    var bordered: Bool = false

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text)
    }

    func makeUIView(context: Context) -> UITextField {
        let field = UITextField()
        field.delegate = context.coordinator
        field.placeholder = placeholder
        field.text = text
        field.keyboardType = keyboard
        field.borderStyle = bordered ? .roundedRect : .none
        field.backgroundColor = .clear
        field.font = UIFont.preferredFont(forTextStyle: .body)
        field.adjustsFontForContentSizeCategory = true
        field.textAlignment = alignment == .trailing ? .right : .left
        field.clearButtonMode = keyboard == .decimalPad ? .whileEditing : .never
        field.autocorrectionType = .no
        field.spellCheckingType = .no
        field.autocapitalizationType = .none
        field.inlinePredictionType = .no
        field.textContentType = nil
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        field.inputAssistantItem.leadingBarButtonGroups = []
        field.inputAssistantItem.trailingBarButtonGroups = []
        field.addTarget(
            context.coordinator,
            action: #selector(Coordinator.textDidChange(_:)),
            for: .editingChanged
        )
        field.inputAccessoryView = context.coordinator.makeDoneBar()
        context.coordinator.field = field
        return field
    }

    func updateUIView(_ field: UITextField, context: Context) {
        // Only push external text in. Resetting the field while it is focused
        // reloads the keyboard and drops keystrokes.
        if !field.isFirstResponder, field.text != text {
            field.text = text
        }
        if field.placeholder != placeholder {
            field.placeholder = placeholder
        }
    }

    func sizeThatFits(
        _ proposal: ProposedViewSize,
        uiView: UITextField,
        context: Context
    ) -> CGSize? {
        let width = proposal.width ?? uiView.intrinsicContentSize.width
        return CGSize(width: width, height: max(uiView.intrinsicContentSize.height, 44))
    }

    final class Coordinator: NSObject, UITextFieldDelegate {
        @Binding var text: String
        weak var field: UITextField?

        init(text: Binding<String>) {
            _text = text
        }

        func makeDoneBar() -> UIToolbar {
            let bar = UIToolbar()
            bar.sizeToFit()
            let done = UIBarButtonItem(
                title: "Done",
                style: .done,
                target: self,
                action: #selector(dismissKeyboard)
            )
            bar.items = [
                UIBarButtonItem(barButtonSystemItem: .flexibleSpace, target: nil, action: nil),
                done,
            ]
            return bar
        }

        @objc func textDidChange(_ sender: UITextField) {
            let updated = sender.text ?? ""
            if text != updated {
                text = updated
            }
        }

        @objc func dismissKeyboard() {
            field?.resignFirstResponder()
        }
    }
}

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
