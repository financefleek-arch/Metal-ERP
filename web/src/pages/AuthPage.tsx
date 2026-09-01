import { useState } from "react";
import { useForm } from "react-hook-form";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

type Mode = "login" | "register";

interface Fields {
  firm_name: string;
  email: string;
  password: string;
}

export function AuthPage() {
  const { login, register: doRegister } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Fields>();

  const onSubmit = handleSubmit(async (v) => {
    setServerError(null);
    try {
      if (mode === "register") await doRegister(v.firm_name.trim(), v.email.trim(), v.password);
      else await login(v.email.trim(), v.password);
    } catch (e) {
      setServerError(e instanceof ApiError ? e.message : "Something went wrong");
    }
  });

  return (
    <div className="grid min-h-[100dvh] place-items-center px-4 py-8">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 font-serif text-2xl font-semibold">Metal ERP</h1>
        <p className="mb-6 text-sm text-muted">
          {mode === "login" ? "Sign in to your firm" : "Register your firm"}
        </p>

        <form onSubmit={onSubmit} className="card space-y-4 p-6">
          {mode === "register" && (
            <div>
              <label className="label">Firm name</label>
              <input
                className="field"
                autoFocus
                {...register("firm_name", { required: "Required", maxLength: 200 })}
              />
              {errors.firm_name && <p className="err">{errors.firm_name.message}</p>}
            </div>
          )}

          <div>
            <label className="label">Email</label>
            <input
              className="field"
              type="email"
              autoComplete="email"
              {...register("email", { required: "Required" })}
            />
            {errors.email && <p className="err">{errors.email.message}</p>}
          </div>

          <div>
            <label className="label">Password</label>
            <input
              className="field"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              {...register("password", {
                required: "Required",
                minLength: { value: 8, message: "At least 8 characters" },
              })}
            />
            {errors.password && <p className="err">{errors.password.message}</p>}
          </div>

          {serverError && <p className="err">{serverError}</p>}

          <button type="submit" className="btn-primary w-full" disabled={isSubmitting}>
            {isSubmitting
              ? "Please wait…"
              : mode === "login"
                ? "Sign in"
                : "Create firm & continue"}
          </button>
        </form>

        <button
          onClick={() => {
            setServerError(null);
            setMode(mode === "login" ? "register" : "login");
          }}
          className="mt-4 text-sm text-accent hover:text-accent-dark"
        >
          {mode === "login" ? "Register a new firm →" : "← Back to sign in"}
        </button>
      </div>
    </div>
  );
}
