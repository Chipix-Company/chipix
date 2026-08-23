# Chipix Studio — user documentation

These files are **end-user guides** shown inside the app under **Documentation**.

They explain how to use Chipix Studio — not how it is built. Keep them focused on what users see and do: design RTL, run verification, read results, and manage projects.

Internal engineering notes, architecture, and implementation plans live elsewhere and must not be copied here.

## For authors

- One topic per `.md` file in this folder.
- Start with `# Title`; the first paragraph becomes the short description in the guide list.
- Link between guides with relative links, for example `[Running verification](running-verification.md)`.
- Use plain language. Avoid server internals, file paths, package names, and algorithm details.
- New guides must be registered in the app documentation catalog **and** in `backend/user_documentation.py` to appear in the product.
