import { forwardRef } from "react";
import { useStates } from "../lib/reference";

type Props = React.SelectHTMLAttributes<HTMLSelectElement>;

/** GST state-code picker. Value is the 2-digit code ("27"), label the name. */
export const StateSelect = forwardRef<HTMLSelectElement, Props>(function StateSelect(props, ref) {
  const { data, isLoading } = useStates();
  return (
    <select ref={ref} className="field" disabled={isLoading} {...props}>
      <option value="">{isLoading ? "Loading…" : "— select state —"}</option>
      {data?.map((s) => (
        <option key={s.code} value={s.code}>
          {s.name} ({s.code})
        </option>
      ))}
    </select>
  );
});
