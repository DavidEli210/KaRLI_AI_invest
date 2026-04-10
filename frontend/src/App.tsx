import { ThemeProvider } from "@/Components/theme-provider"
import { Routes, Route } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage/DashboardPage";
import SignUpPage from "./pages/SignUpPage/SignUpPage";
import LoginPage from "./pages/LoginPage/LoginPage";
import ConfirmSignUpPage from "./pages/ConfirmSignUpPage/ConfirmSignUpPage";
import { PrivateRoute } from "./Components/private-route";

export default function App() {
    return (
        <ThemeProvider defaultTheme="dark" storageKey="vite-ui-theme">
            <Routes>
                <Route path='/' element={<PrivateRoute/>}>
                    <Route path='/' element={<DashboardPage/>}/>
                </Route>
                <Route path="/signup" element={<SignUpPage />} />
                <Route path="/confirm-signup" element={<ConfirmSignUpPage />} />
                <Route path="/login" element={<LoginPage />} />
            </Routes>
        </ThemeProvider>
    );
}