import { useEffect, type ReactNode } from "react";

/**
 * Public, no-login page: the account-deletion request page that Google Play asks for. The privacy policy itself
 * lives on https://fleekfintech.com/privacy-policy/ (versioned WordPress page, Section 12 covers Metal ERP).
 */

const COMPANY = "Fleek Fintech LLP";
const EMAIL = "contact@fleekfinance.in";
const UPDATED = "22 October 2026";
const PRIVACY_POLICY = "https://fleekfintech.com/privacy-policy/";

function Page({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    const before = document.title;
    document.title = `${title} · Metal ERP`;
    return () => {
      document.title = before;
    };
  }, [title]);
  return (
    <main className="mx-auto max-w-2xl px-4 py-8 text-[15px] leading-relaxed text-ink">
      <h1 className="font-serif text-3xl font-semibold">{title}</h1>
      <p className="mt-1 text-sm text-muted">
        {COMPANY} · Last updated {UPDATED}
      </p>
      <div className="mt-6 space-y-6">{children}</div>
    </main>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 font-serif text-xl font-semibold">{title}</h2>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function List({ items }: { items: ReactNode[] }) {
  return (
    <ul className="list-disc space-y-1 pl-5">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

const link = "text-accent underline underline-offset-2";

export function DeleteAccountPage() {
  const subject = encodeURIComponent("Delete my Metal ERP account");
  const body = encodeURIComponent(
    "Please delete the Metal ERP account and data for this firm.\n\nFirm name:\nLogin email:\n",
  );
  return (
    <Page title="Delete your account">
      <p>
        A shop can ask us to delete its Metal ERP account and the data in it. Only the shop&apos;s owner can make
        the request.
      </p>

      <Section title="How to ask">
        <ol className="list-decimal space-y-1 pl-5">
          <li>
            Email us from the login email address of the account (or an address we can match to it) with the
            subject <em>Delete my Metal ERP account</em>, giving the firm name.
          </li>
          <li>We reply to confirm it is you and that you want everything deleted.</li>
          <li>We delete the account and its data and confirm when it is done.</li>
        </ol>
        <p className="pt-1">
          <a
            className="btn-primary"
            href={`mailto:${EMAIL}?subject=${subject}&body=${body}`}
          >
            Email a deletion request
          </a>
        </p>
      </Section>

      <Section title="What is deleted">
        <List
          items={[
            "The firm's account and user logins.",
            "Customers, suppliers, items and photos, invoices, payments, reminders, orders and uploaded documents.",
            "Tally data and any Tally backups synced to us, and the WhatsApp message log.",
          ]}
        />
        <p>
          Deletion is permanent. Download or export what you need first. We carry it out within one month of
          confirming the request, and backups age out shortly after. We may keep limited records where the
          law requires it.
        </p>
      </Section>

      <Section title="Delete only some of your data">
        <p>
          You do not have to close the account. Email us and say what you want removed, for example a customer, a supplier, some items or
          photos, or an uploaded document, and we delete it within 30 days of confirming the request. Many of these you can also remove yourself
          in the app.
        </p>
        <p>
          Finalised invoices, and the payment records tied to them, are accounting records. The app does not allow them to be deleted, and we keep
          them while you are required to.
        </p>
      </Section>

      <Section title="If you are a shop's customer">
        <p>
          To have data about you removed, ask the shop you bought from. You can also write to {EMAIL} and
          we will take it up with them.
        </p>
      </Section>

      <p className="text-sm text-muted">
        See also our{" "}
        <a className={link} href={PRIVACY_POLICY}>
          privacy policy
        </a>
        .
      </p>
    </Page>
  );
}
