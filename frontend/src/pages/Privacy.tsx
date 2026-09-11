import LegalLayout, { SUPPORT_WHATSAPP, SUPPORT_WHATSAPP_LINK } from "../components/LegalLayout";

export default function Privacy() {
  return (
    <LegalLayout title="Privacy Policy">
      <p>
        This policy explains how Qlass Edtech ("we"), operating Skoolgpt, handles personal data under the Digital
        Personal Data Protection Act, 2023 and the Information Technology Act, 2000. Qlass Edtech is the data
        fiduciary for the data described here.
      </p>

      <h2>1. What we collect</h2>
      <ul>
        <li><strong>Account details:</strong> name, WhatsApp/phone number, class, board and school; a parent's name and number where linked.</li>
        <li><strong>Tutoring content:</strong> the messages, voice notes, photos and documents you send to the tutor, and the answers it gives.</li>
        <li><strong>Learning data:</strong> quiz scores, topics studied, progress summaries and credit usage.</li>
        <li><strong>Payment references:</strong> order and payment IDs and amounts from Razorpay. We never see or store card, UPI or bank details — Razorpay handles those.</li>
        <li><strong>Technical data:</strong> device and browser information, push-notification tokens and basic logs needed to run and secure the service.</li>
        <li><strong>Google sign-in:</strong> if you sign in with Google, the name and email Google provides.</li>
      </ul>

      <h2>2. Why we use it</h2>
      <ul>
        <li>to answer your questions and run quizzes, worksheets and progress tracking;</li>
        <li>to verify your phone number, secure your account and prevent abuse;</li>
        <li>to bill you, manage credits and plans, and handle refunds;</li>
        <li>to report a student's progress to their school and parents, where they are linked;</li>
        <li>to send service messages on WhatsApp such as codes, receipts, digests and reminders;</li>
        <li>to improve the tutor, using aggregated or de-identified data wherever possible.</li>
      </ul>
      <p>We rely on your consent, or on a parent's or school's consent for a child, and on the performance of the service you asked for.</p>

      <h2>3. Children</h2>
      <p>
        Most Skoolgpt users are under 18. We process a child's data only with verifiable consent from a parent or
        legal guardian, given directly, or through the school that enrolled the child on the parents' behalf. We
        do not track children for advertising, profile them for behavioural targeting, or show them targeted ads.
        A parent may withdraw consent at any time and ask us to delete the child's account and data by contacting
        us as described below; the account stops working once consent is withdrawn.
      </p>

      <h2>4. Who we share it with</h2>
      <p>We share data only with processors that help us run the service, under contracts that limit their use of it:</p>
      <ul>
        <li><strong>WATI and Meta (WhatsApp)</strong> — to deliver and receive WhatsApp messages;</li>
        <li><strong>Anthropic and Google</strong> — AI providers that generate the tutor's responses from the content you send;</li>
        <li><strong>Razorpay</strong> — to process payments and subscriptions;</li>
        <li><strong>Firebase (Google)</strong> — for push notifications in the web app;</li>
        <li>your <strong>school and linked parents</strong> — progress and usage reports, where you are enrolled through a school or a parent account is linked;</li>
        <li>authorities, where the law requires it.</li>
      </ul>
      <p>We do not sell personal data, and we do not share it with advertisers.</p>

      <h2>5. Retention</h2>
      <p>
        Chat history and learning data are kept while your account is active and for up to 12 months after it
        becomes inactive, then deleted or de-identified. Payment records are kept for as long as tax and accounting
        law requires. You or your parent can ask for earlier deletion at any time; we will complete it within 30
        days unless the law requires us to keep specific records.
      </p>

      <h2>6. Your rights</h2>
      <ul>
        <li><strong>Access:</strong> ask for a summary of the personal data we hold about you.</li>
        <li><strong>Correction:</strong> update your name, class, school or contact details from your account page or by asking us.</li>
        <li><strong>Deletion:</strong> ask us to delete your account and data. Parents can request this for a child.</li>
        <li><strong>Withdraw consent:</strong> at any time, with the same ease it was given.</li>
        <li><strong>Grievance:</strong> raise a complaint with our Grievance Officer, and if unresolved, with the Data Protection Board of India.</li>
      </ul>

      <h2>7. Security</h2>
      <p>
        Data is stored on servers in India or with the processors listed above, encrypted in transit, with access
        limited to staff who need it. One-time codes protect account access. No system is perfectly secure; if a
        breach affects you, we will notify you and the Data Protection Board as the law requires.
      </p>

      <h2>8. Cookies and local storage</h2>
      <p>
        The web app stores a sign-in token and your theme preference in your browser. We do not use third-party
        advertising cookies.
      </p>

      <h2>9. Grievance Officer</h2>
      <p>
        Swati Jha, Grievance Officer, Qlass Edtech
        <br />
        Phone: <a href="tel:+919229261314">+91 92292 61314</a>
        <br />
        WhatsApp support: <a href={SUPPORT_WHATSAPP_LINK}>{SUPPORT_WHATSAPP}</a>
        <br />
        Address: SkoolGPT, 1st and 2nd Floor, Near RPS Engineering College, Kothwan, Khagaul, Danapur, Patna, Bihar,
        India – 801503
      </p>
      <p>We acknowledge every request or complaint and respond within 30 days.</p>

      <h2>10. Changes</h2>
      <p>We will announce material changes to this policy on WhatsApp or on this page before they take effect.</p>
    </LegalLayout>
  );
}
