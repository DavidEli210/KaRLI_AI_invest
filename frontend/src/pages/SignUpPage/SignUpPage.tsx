import { z } from "zod"

import { SignupForm } from "@/Components/signup-form"
import { signupFormSchema } from "@/schemas/signup-form-schema"
import { useNavigate } from "react-router-dom"
import { signUpWithCognito } from "@/lib/cognitoAuth"
import { useState } from "react"

type CognitoError = {
  message?: string
  code?: string
}

function SignUpPage() {
    const navigate = useNavigate()
    const [error, setError] = useState<string>()

    const handleSubmit = async (values: z.infer<typeof signupFormSchema>) => {
      setError(undefined)
      try {
        await signUpWithCognito({
          username: values.username,
          email: values.email,
          password: values.password,
          alpacaKey: values.brokerApiKey,
          alpacaSecret: values.brokerApiSecret,
        })
        navigate("/confirm-signup", {
          state: {
            username: values.username,
            email: values.email,
          },
        })
      } catch (caughtError) {
        const cognitoError = caughtError as CognitoError
        setError(cognitoError.message ?? "Unable to sign up. Please verify your details and try again.")
      }
    }
  
    return (
      <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
        <div className="w-full max-w-sm">
          <SignupForm error={error} onSumbit={handleSubmit} />
        </div>
      </div>
    )
  }
  
  export default SignUpPage  
