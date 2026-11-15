import SwiftUI

struct CustomerEntryView: View {
    @Bindable var session: AppSession
    @State private var bankLinkId = ""
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var modeHint = "Getting ready…"

    private let client = APIClient()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("See safe-to-spend, upcoming bills, and what-if decisions before money moves.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                Section("Get started") {
                    Text(modeHint)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    TextField("Optional demo bank link", text: $bankLinkId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("Only needed for live demos. Leave blank to explore with sample finances.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("Connect (optional)")
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }

                Section {
                    Button {
                        Task { await bindAndContinue() }
                    } label: {
                        if isWorking {
                            ProgressView()
                        } else {
                            Text("Connect & continue")
                        }
                    }
                    .disabled(bankLinkId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isWorking)

                    Button("Continue with sample data") {
                        session.completeOnboarding(nessieCustomerId: nil)
                    }
                    .disabled(isWorking)
                }
            }
            .navigationTitle("Welcome to Centsible")
            .task { await loadSession() }
        }
    }

    private func loadSession() async {
        do {
            let sessionInfo = try await client.fetchSession()
            if sessionInfo.mode.lowercased() == "live" {
                modeHint = sessionInfo.writesEnabled
                    ? "Live demo bank is available. Connect a link or continue with the current mapping."
                    : "Live data is available. You can still explore forecasts even if Demo Mode writes are off."
            } else {
                modeHint = "Sample finances are ready — explore every tab without connecting."
            }
            if let bound = sessionInfo.boundNessieCustomerId, !bound.isEmpty {
                bankLinkId = bound
            }
        } catch {
            modeHint = "Start the Centsible backend on this Mac (port 8000), then try again."
        }
    }

    private func bindAndContinue() async {
        let trimmed = bankLinkId.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            let result = try await client.bindCustomer(nessieCustomerId: trimmed)
            session.completeOnboarding(nessieCustomerId: result.boundNessieCustomerId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    CustomerEntryView(session: AppSession())
}
