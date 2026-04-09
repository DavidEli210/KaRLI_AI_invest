import { Navigate, Outlet } from "react-router-dom"
import { isAuthenticated } from "@/lib/cognitoAuth"

export function PrivateRoute() {
	const auth = isAuthenticated()
	return auth ? <Outlet /> : <Navigate to="/login" />;
}
