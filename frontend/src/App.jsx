import React from "react";
import DesktopApp from "./DesktopApp";
import ActivationGate from "./components/desktop/ActivationGate";
import UpdateGate from "./components/desktop/UpdateGate";
import { Sentry } from "./lib/observability";

function AppErrorFallback() {
  return (
    <div className="chipverify-app-error-fallback" role="alert">
      <h1>Something went wrong</h1>
      <p>ChipVerify hit an unexpected error. Reload the app to continue.</p>
      <button type="button" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  );
}

function App() {
  return (
    <Sentry.ErrorBoundary fallback={AppErrorFallback} showDialog={import.meta.env.PROD}>
      <UpdateGate>
        <ActivationGate>
          <DesktopApp />
        </ActivationGate>
      </UpdateGate>
    </Sentry.ErrorBoundary>
  );
}

export default App;



