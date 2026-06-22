def physics_to_latex(text):
    """
    First Lecture2TeX parser.
    Very simple version.
    """

    replacements = {
        "squared": r"$^2$",
        "of x": r"$(x)$",
        "epsilon": r"$\epsilon$",
        "psi": r"$\psi$",
        "phi": r"$\phi$",
        "theta": r"$\theta$",
        "lambda": r"$\lambda$",
        "omega": r"$\omega$",
        "alpha": r"$\alpha$",
        "beta": r"$\beta$",
        "gamma": r"$\gamma$",
        "delta": r"$\delta$",
        "nabla": r"$\nabla$",
    }

    processed = text.lower()

    for phrase, latex in replacements.items():
        processed = processed.replace(phrase, latex)

    return processed