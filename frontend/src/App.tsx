import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { Shell } from "./components/Shell";
import { LoginPage } from "./routes/LoginPage";
import { DashboardPage } from "./routes/DashboardPage";
import { PredictiveEnginePage } from "./routes/predictive/PredictiveEnginePage";
import { RunningSimulationPage } from "./routes/predictive/RunningSimulationPage";
import { PersonaConfigurationPage } from "./routes/predictive/PersonaConfigurationPage";
import { JourneyGraphPage } from "./routes/journey/JourneyGraphPage";
import { CalibrationPage } from "./routes/calibration/CalibrationPage";
import { ModelCalibrationSettingsPage } from "./routes/settings/ModelCalibrationSettingsPage";

export function App() {
  return (
    <AuthProvider>
      <Routes>
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
          <Route path="/settings/model-calibration" element={<ModelCalibrationSettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
