import LegalLayout, { SUPPORT_WHATSAPP, SUPPORT_WHATSAPP_LINK } from "../components/LegalLayout";

export default function Refunds() {
  return (
    <LegalLayout title="Refund & Cancellation Policy">
      <p>This policy applies to all purchases made for Skoolgpt, operated by Qlass Edtech.</p>

      <h2>1. Credit top-ups</h2>
      <ul>
        <li>A credit top-up is refundable in full if you ask within 7 days of payment and none of it has been used.</li>
        <li>Credits that have been used, even partly, are not refundable. A partly used top-up is not refunded pro rata.</li>
        <li>Free trial or goodwill credits have no cash value and are never refundable.</li>
        <li>To request a refund, message our support WhatsApp with the phone number on the account and the payment ID from your Razorpay receipt.</li>
      </ul>

      <h2>2. Annual plans</h2>
      <ul>
        <li>You may cancel an annual plan at any time using the "Cancel subscription" option on your account page, or by asking support.</li>
        <li>Cancelling stops auto-renewal. Your access continues until the end of the period you have already paid for; no further charges are made.</li>
        <li>A plan cancelled within 7 days of first purchase, with no tutoring usage in that time, is refunded in full. After that, or once the plan has been used, the current period is not refunded.</li>
        <li>If a renewal is charged in error after you cancelled, we refund it in full.</li>
      </ul>

      <h2>3. Failed or duplicate payments</h2>
      <p>
        If money left your account but credits or your plan did not appear, or you were charged twice, contact
        support with the payment ID. Verified duplicate or failed-but-debited payments are refunded in full.
      </p>

      <h2>4. How refunds are paid</h2>
      <p>
        Approved refunds are returned to the original payment method through Razorpay within 7 to 10 working days
        of approval. Your bank or UPI app may take a few extra days to show it. We cannot refund to a different
        account or in cash.
      </p>

      <h2>5. Disputes</h2>
      <p>
        Contact us first on WhatsApp at <a href={SUPPORT_WHATSAPP_LINK}>{SUPPORT_WHATSAPP}</a>; we reply within 30
        days and usually much sooner. If you are not satisfied, you may raise the matter with our Grievance Officer
        (see the Privacy Policy) or with the consumer forum for your district.
      </p>
    </LegalLayout>
  );
}
