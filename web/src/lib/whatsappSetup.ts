export interface TemplateCounts {
  approved: number;
  pending: number;
  rejected: number;
  total: number;
}

export type Mark = "done" | "waiting" | "problem" | "todo";

export interface Step {
  mark: Mark;
  title: string;
  hint?: string;
}

/**
 * What is done and what is still pending after the owner presses Connect. Only what Fleek can tell
 * us is ticked: the number, and how many of the shop's messages WhatsApp has approved. The payment
 * method is a reminder, not a tick, because nothing here can see whether one has been added.
 */
export function setupSteps(registered: boolean, templates: TemplateCounts | null | undefined): Step[] {
  const steps: Step[] = [
    registered
      ? { mark: "done", title: "Number connected and registered" }
      : {
          mark: "problem",
          title: "Registration with WhatsApp is not finished",
          hint: "Nothing can be sent until it is. Use Retry below, or ask Fleek.",
        },
  ];
  if (templates && templates.total > 0) {
    const { approved, pending, rejected, total } = templates;
    if (rejected > 0) {
      steps.push({
        mark: "problem",
        title: `Messages approved by WhatsApp: ${approved} of ${total}`,
        hint: `${rejected} ${rejected === 1 ? "was" : "were"} refused. Fleek is told and will fix it.`,
      });
    } else if (approved < total) {
      steps.push({
        mark: "waiting",
        title: `Messages approved by WhatsApp: ${approved} of ${total}`,
        hint:
          pending > 0
            ? "Usually within a day. Each message works as soon as it is approved."
            : undefined,
      });
    } else {
      steps.push({ mark: "done", title: `All ${total} messages approved by WhatsApp` });
    }
  } else if (registered) {
    steps.push({
      mark: "waiting",
      title: "Messages are being sent to WhatsApp for approval",
      hint: "They show here once WhatsApp has them.",
    });
  }
  steps.push({
    mark: "todo",
    title: "Payment method added on WhatsApp",
    hint: "WhatsApp bills you directly for messages. Add one in your WhatsApp account settings, or sending is blocked.",
  });
  return steps;
}

/** True while the shop is connected but some message is still waiting for WhatsApp (the card then says "Almost ready"). */
export function almostReady(templates: TemplateCounts | null | undefined): boolean {
  return !!templates && templates.total > 0 && templates.approved < templates.total && templates.rejected === 0;
}
