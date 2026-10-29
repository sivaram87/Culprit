import SwiftUI

// A tiny coffee shop with one watched flag, `new_checkout`. Variant "on" is deliberately buggy:
// its buttons crash, freeze (ANR / app hang) or log a handled error, so Culprit has something to find.

@main
struct CulpritDemoApp: App {
    init() { Backend.start() }  // wire Flags and call Flags.tag() before any UI reads a flag

    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

struct ContentView: View {
    @State private var variant = Flags.variant("new_checkout")
    @State private var killed = Flags.isKilled("new_checkout")

    var body: some View {
        NavigationStack {
            List {
                Section("Shop") {
                    LabeledContent("Coffee beans, 1 kg", value: "$24")
                    NavigationLink("Checkout") { CheckoutView() }
                }
                Section {
                    LabeledContent("Backend", value: Backend.name)
                    LabeledContent("User", value: String(Backend.user.prefix(8)))
                    LabeledContent("new_checkout", value: variant)
                    LabeledContent("killswitch_new_checkout", value: killed ? "on" : "off")
                    Button("Refresh flags") { Backend.refresh(update) }
                    if Backend.canSwitchUser {
                        Button("Become a new user") { Backend.newUser(); Backend.refresh(update) }
                    }
                } header: {
                    Text("Culprit demo")
                } footer: {
                    Text("The variant shown is what Flags.variant() returns: control when the kill switch is on.")
                }
            }
            .navigationTitle("Bean There")
            .onAppear { Backend.refresh(update) }
        }
    }

    private func update() {
        variant = Flags.variant("new_checkout")
        killed = Flags.isKilled("new_checkout")
    }
}

struct CheckoutView: View {
    var body: some View {
        // Read the watched flag only through the wrapper, so a kill switch sends users to control.
        if Flags.variant("new_checkout") == "on" { NewCheckout() } else { ClassicCheckout() }
    }
}

struct ClassicCheckout: View {
    @State private var paid = false

    var body: some View {
        List {
            Section(footer: Text("Control. Nothing here breaks.")) {
                Button("Pay $24") { paid = true }
            }
        }
        .navigationTitle("Checkout")
        .alert("Paid. Thanks!", isPresented: $paid) {}
    }
}

struct NewCheckout: View {
    @State private var saved = false

    var body: some View {
        List {
            Section {
                Button("Pay $24 with one tap", role: .destructive) { Backend.crash() }
                Button("Apply coupon") { Backend.freeze() }
                Button("Save card") { Backend.nonFatal(); saved = true }
            } header: {
                Text("New checkout (variant on)")
            } footer: {
                Text("Pay crashes the app. Apply coupon blocks the main thread for 6 seconds. Save card logs a handled error.")
            }
        }
        .navigationTitle("Checkout")
        .alert("Card saved (an error was logged)", isPresented: $saved) {}
    }
}
