/** The same rule as the server (api/app/services/passwords.py): 8+ characters with a digit or a special
 *  character. The server also refuses very common passwords; it has the last word. */
export function passwordProblem(pw: string): string | null {
  if (pw.length < 8) return "At least 8 characters";
  if (!/[\d\W_]/.test(pw)) return "Add a digit or a symbol";
  return null;
}

/** The email local part and the firm name are refused as passwords by the server; warn early. */
export const PASSWORD_HINT = "8+ characters with a digit or symbol";
