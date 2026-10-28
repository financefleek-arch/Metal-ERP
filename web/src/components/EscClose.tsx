import { useDialogKeys } from "../lib/keyboard";

/** Drop inside a conditionally rendered overlay to give it Esc-to-close and
 *  focus restore, where the host component can't call the hook itself. */
export function EscClose({ onClose }: { onClose: () => void }) {
  useDialogKeys(onClose);
  return null;
}
