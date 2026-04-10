import { z } from "zod"

import { LoginForm } from "@/Components/login-form"
import { loginFormSchema } from "@/schemas/login-form-schema"
import { signInWithCognito } from "@/lib/cognitoAuth"

import { useNavigate } from "react-router-dom"
import { useState } from "react"


function LoginPage() {
    const navigate = useNavigate()
    const [error, setError] = useState<{ type: string, message: string }>()

    const handleSubmit = async (values: z.infer<typeof loginFormSchema>) => {
        try {
            await signInWithCognito(values.username, values.password)
            navigate("/")
        } catch {
            setError({
                type: "incorrect login",
                message: "Username or password is incorrect."
            })
        }
    }

    return (
        <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
            <div className="w-full max-w-md">
                <LoginForm error={error} onSumbit={handleSubmit} />
            </div>
        </div>
    )
}

export default LoginPage
