import { FormEvent, useState } from "react"
import { Link, useLocation, useNavigate } from "react-router-dom"
import { confirmSignUpWithCognito } from "@/lib/cognitoAuth"

type CognitoError = {
  message?: string
}

type ConfirmSignUpLocationState = {
  username?: string
  email?: string
}

export default function ConfirmSignUpPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const locationState = (location.state as ConfirmSignUpLocationState | null) ?? null

  const [username, setUsername] = useState(locationState?.username ?? "")
  const [code, setCode] = useState("")
  const [error, setError] = useState<string>()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleConfirm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(undefined)
    setIsSubmitting(true)

    try {
      await confirmSignUpWithCognito(username.trim(), code.trim())
      navigate("/login")
    } catch (caughtError) {
      const cognitoError = caughtError as CognitoError
      setError(cognitoError.message ?? "Unable to confirm your account. Please try again.")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
      <div className="w-full max-w-sm rounded-lg border p-6">
        <h1 className="mb-2 text-xl font-bold">Confirm your account</h1>
        <p className="mb-4 text-sm text-muted-foreground">
          Enter the confirmation code sent to your email{locationState?.email ? ` (${locationState.email})` : ""}.
        </p>

        <form className="space-y-4" onSubmit={handleConfirm}>
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="confirm-username">Username</label>
            <input
              id="confirm-username"
              className="w-full rounded-md border bg-transparent px-3 py-2 text-sm"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </div>

          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="confirm-code">Confirmation Code</label>
            <input
              id="confirm-code"
              className="w-full rounded-md border bg-transparent px-3 py-2 text-sm"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
          </div>

          {error ? <p className="text-sm text-red-500">{error}</p> : null}

          <button
            type="submit"
            className="w-full rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
            disabled={isSubmitting}
          >
            {isSubmitting ? "Confirming..." : "Confirm account"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-muted-foreground">
          Already confirmed? <Link to="/login" className="underline">Go to login</Link>
        </p>
      </div>
    </div>
  )
}
