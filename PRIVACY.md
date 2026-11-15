# Privacy and data handling

This extension connects a project's configured Facebook Pages to workflows. When
operators invoke publishing or reply actions, post text, captions, remote photo
URLs, links and reply text are sent to Meta's Graph API. Meta may retrieve the
remote photo URL. Reads and signed callbacks can contain Page, post, comment and
author identifiers, names, comment text and timestamps. These become workflow
inputs and can be retained in the host's event and job histories according to the
operator's retention settings.

Page tokens and app secrets belong in the selected project's encrypted connection
secret storage. They must not appear in action results, provider error text,
workflow inputs or URLs. A webhook URL and its verify token are credentials;
share them only with the intended Meta app administrators. The extension has no
analytics endpoint or separate vendor-operated storage. Provider requests are
restricted to graph.facebook.com; remote photo fetching is Meta's responsibility.

Each Page connection belongs to one project. A callback anchor accepts only its
explicit Page allowlist and preserves the authenticated anchor connection and
project. It does not discover or rewrite another project's connection. Delete or
disable callback registrations and revoke Page tokens to stop future access;
remove retained events and jobs through the host's supported retention controls.
Revoking access does not remove content already published on Facebook.

Meta's data practices and permission review rules also apply. Operators are
responsible for lawful use of Page conversations, access controls and retention.
