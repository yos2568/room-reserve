# Complaint form

Signed-in users can open `/th/complaints/` from the account menu or Help.
The form accepts a subject, optional active room, and message. Sender identity
comes from the account, including whether the reply email has been verified.
It does not require booking eligibility, so users awaiting approval can report
problems. People unable to sign in can use the direct support email on Login.

Messages go to `COMPLAINT_RECIPIENT_EMAIL`. It has no default: production refuses to start without it, and dev/test use `complaints@localhost.test`. Set the real faculty contact only in the production environment.
The sender cannot change this destination through the form. SMTP uses the
configured application sender; Reply-To points to the account email.

Submissions are stored in the existing notification outbox, with a unique
reference and duplicate protection for repeat submission of the same form.
The limit is three new submissions per account per hour. The success notice
means queued for delivery, not delivered or resolved. Existing outbox workers
retry failures; staff can inspect the destination and status on the Outbox page.
Messages are private to the existing staff/outbox access boundary and recipient
mailbox. There is no public complaint listing or automatic AI processing.

## Deployment

Apply migration `0013_notification_external_recipient`, build CSS, collect static
files, and restart application and worker processes using the normal deployment
procedure. Ensure the existing mail worker remains running. No new provider or
SMTP credentials are needed. Test with the in-memory backend before a real send.

This feature is an email intake form. Ticket assignment, resolution tracking,
attachments and student-visible progress history are not included.
