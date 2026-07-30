import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { Shell } from "./components/Shell";
import { LandingPage } from "./routes/LandingPage";
import { LoginPage } from "./routes/LoginPage";
import { DashboardPage } from "./routes/DashboardPage";
import { PredictiveEnginePage } from "./routes/predictive/PredictiveEnginePage";
import { RunningSimulationPage } from "./routes/predictive/RunningSimulationPage";
import { PersonaConfigurationPage } from "./routes/predictive/PersonaConfigurationPage";
import { JourneyGraphPage } from "./routes/journey/JourneyGraphPage";
import { CalibrationPage } from "./routes/calibration/CalibrationPage";
import { ModelCalibrationSettingsPage } from "./routes/settings/ModelCalibrationSettingsPage";
import { GeneralSettingsPage } from "./routes/settings/GeneralSettingsPage";
import { BillingSettingsPage } from "./routes/settings/BillingSettingsPage";
import { TeamSettingsPage } from "./routes/settings/TeamSettingsPage";
import { IntegrationsSettingsPage } from "./routes/settings/IntegrationsSettingsPage";
import { SecurityLogsPage } from "./routes/settings/SecurityLogsPage";
import { GettingStartedPage } from "./routes/GettingStartedPage";

function HomeRoute() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-on-surface-variant">
        Loading…
      </div>
    );
  }

  if (user !== null) {
    return <Navigate to="/dashboard" replace />;
  }

  return <LandingPage />;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/predictive" element={<PredictiveEnginePage />} />
        <Route path="/predictive/runs/:runId" element={<RunningSimulationPage />} />
        <Route path="/predictive/personas/new" element={<PersonaConfigurationPage />} />
        <Route path="/predictive/personas/:personaId" element={<PersonaConfigurationPage />} />
        <Route path="/journey" element={<JourneyGraphPage />} />
        <Route path="/calibration" element={<CalibrationPage />} />
        <Route path="/settings/general" element={<GeneralSettingsPage />} />
        <Route path="/settings/billing" element={<BillingSettingsPage />} />
        <Route path="/settings/team" element={<TeamSettingsPage />} />
        <Route path="/settings/model-calibration" element={<ModelCalibrationSettingsPage />} />
        <Route path="/settings/integrations" element={<IntegrationsSettingsPage />} />
        <Route path="/settings/security" element={<SecurityLogsPage />} />
        <Route path="/getting-started" element={<GettingStartedPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
