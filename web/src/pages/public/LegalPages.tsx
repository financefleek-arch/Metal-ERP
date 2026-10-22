import { useEffect, type ReactNode } from "react";

/**
 * Public, no-login pages: the privacy policy and the account-deletion request page that Google Play
 * asks for. Kept as plain prose so they read the same in a browser, in the Android app and to a reviewer.
 */

const COMPANY = "Fleek Fintech LLP";
const ADDRESS = "Pinnacle, The Woods, Wakad, Pune 411057, India";
const EMAIL = "contact@fleekfinance.in";
const PHONE = "+91 8956506362";
const UPDATED = "22 October 2026";
const PARENT_POLICY = "https://fleekfintech.com/privacy-policy/";

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

export function PrivacyPage() {
  return (
    <Page title="Privacy policy">
      <p>
        This policy covers <strong>Metal ERP</strong>, the billing, collections and ordering service for
        metal and bartan shops, including its Android app (listed on Google Play as Tally+). It is
        provided by {COMPANY}, {ADDRESS}. Our general policy for our other products is at{" "}
        <a className={link} href={PARENT_POLICY}>
          fleekfintech.com/privacy-policy
        </a>
        .
      </p>

      <Section title="Who is responsible for what">
        <List
          items={[
            <>
              <strong>The shop</strong> (the business that signs up) decides what customer, supplier and
              invoice data it puts into Metal ERP. For that data the shop is the controller and we are
              its processor: we store and handle it on the shop&apos;s instructions.
            </>,
            <>
              <strong>We</strong> are the controller of the shop&apos;s own account details, such as
              login emails, and of the technical logs needed to run and secure the service.
            </>,
          ]}
        />
        <p>
          If you are a shop&apos;s customer and want to see, correct or delete data about you, please ask
          that shop first. You can also write to us (see Contact) and we will pass it on or help.
        </p>
      </Section>

      <Section title="What we collect">
        <p>
          <strong>Account details.</strong> Firm name, user names and email addresses, the role of each
          user, and a password, which is stored only in a hashed form that we cannot read.
        </p>
        <p>
          <strong>Business records the shop enters or imports.</strong> Customers and suppliers (name,
          phone number, address, GSTIN, opening balance), items and prices, item photos, invoices and
          payments, payment reminders, purchase bills and supplier price lists the shop uploads, and
          orders placed through a shop&apos;s catalog link (the customer&apos;s name, phone number and the
          items ordered).
        </p>
        <p>
          <strong>Photos.</strong> If a user adds an item photo, the phone&apos;s browser (Chrome) asks to use the
          camera or the gallery, and only for that photo. Nothing is accessed in the background.
        </p>
        <p>
          <strong>Tally data (optional).</strong> If a shop installs our Tally companion agent, we receive
          the Tally ledgers, stock items and vouchers it syncs, and, if the shop turns it on, a backup copy
          of the Tally company file.
        </p>
        <p>
          <strong>WhatsApp messages.</strong> When a shop sends an invoice, statement, reminder or order
          update by WhatsApp, we send the message and its recipient&apos;s phone number to WhatsApp (Meta),
          and we keep the delivery status.
        </p>
        <p>
          <strong>Technical data.</strong> IP address, browser or device type, and error and security logs.
        </p>
        <p>
          <strong>What the app does not use.</strong> Your location, your phone&apos;s contact list, or the
          microphone. We do not run advertising and we do not sell personal data.
        </p>
      </Section>

      <Section title="How we use it">
        <List
          items={[
            "To provide Metal ERP: create invoices, track payments, send messages the shop asks for, and sync with Tally.",
            "To keep the service secure, prevent misuse and fix faults.",
            "To meet legal obligations, and to answer your requests.",
          ]}
        />
      </Section>

      <Section title="Who we share it with">
        <p>Only the providers that help us run the service, and only what the feature needs:</p>
        <List
          items={[
            "Our hosting provider and Cloudflare R2, where the service and its files (photos, PDFs, Tally backups) are stored.",
            "WhatsApp (Meta), to deliver the messages a shop sends. If a shop connects its own WhatsApp number, the Facebook SDK loads on that screen.",
            "Google Fonts, which supplies the typefaces: your browser fetches them from Google when a page opens.",
            "If a shop switches on document reading or voice entry, the file or audio is processed by our AI providers (Anthropic, and Deepgram for speech) to return the result.",
          ]}
        />
        <p>
          We may also disclose data when the law requires it. We do not share it with advertisers or data
          brokers.
        </p>
      </Section>

      <Section title="How long we keep it">
        <p>
          We keep a shop&apos;s data while its account is active. If the shop closes its account or asks us
          to delete it, we delete the data within one month, and backups age out shortly afterwards. We may
          keep limited records for longer where the law requires it, or to settle a dispute.
        </p>
      </Section>

      <Section title="Security">
        <p>
          Data travels over encrypted connections (HTTPS). Each shop&apos;s data is kept apart from other
          shops&apos;, passwords are stored hashed, and access is limited to the people who need it to run
          the service. No system is perfectly secure, and we will tell affected shops about a breach as the
          law requires.
        </p>
      </Section>

      <Section title="Your rights">
        <p>
          Under India&apos;s Digital Personal Data Protection Act, 2023, you can ask to access, correct or
          delete your personal data and to withdraw consent. To delete a shop&apos;s account and its data,
          use the{" "}
          <a className={link} href="/delete-account">
            account deletion page
          </a>
          . For anything else, or to raise a complaint, write to {EMAIL}.
        </p>
      </Section>

      <Section title="Children">
        <p>Metal ERP is for businesses and is not meant for anyone under 18.</p>
      </Section>

      <Section title="Changes">
        <p>
          If we change this policy we will update the date above and, for a material change, tell shops in
          the app or by email.
        </p>
      </Section>

      <Section title="Contact">
        <p>
          {COMPANY}, {ADDRESS}
          <br />
          Email:{" "}
          <a className={link} href={`mailto:${EMAIL}`}>
            {EMAIL}
          </a>{" "}
          · Phone: {PHONE}
        </p>
      </Section>
    </Page>
  );
}

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

      <Section title="If you are a shop's customer">
        <p>
          To have data about you removed, ask the shop you bought from. You can also write to {EMAIL} and
          we will take it up with them.
        </p>
      </Section>

      <p className="text-sm text-muted">
        See also our{" "}
        <a className={link} href="/privacy">
          privacy policy
        </a>
        .
      </p>
    </Page>
  );
}
