# Security policy

Issues should be fixed as soon as possible, and information about a security
issue should be published once it is fixed. Report serious issues privately
and keep them under wraps until a fix is released: use
[GitHub's private vulnerability reporting](https://github.com/pycalendar/calendaring/security/advisories/new)
(the "Report a vulnerability" button on the Security tab). Use the public
issue tracker only for issues that are not sensitive.

There are no long-term-support releases, but the maintainer will consider
backporting a security fix where it is relevant. Maintenance is partly done
on a hobby basis, so no response times are guaranteed.

# Known security issues and risks

None yet: there is no released code. Risks inherited from `caldav` (DNS-based
auto-discovery, expansion of recurring items on untrusted search input) are
listed in [caldav's SECURITY.md](https://github.com/python-caldav/caldav/blob/master/SECURITY.md)
and will be assessed here as the backends are written.
