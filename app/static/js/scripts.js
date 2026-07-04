function animateEntrance() {
    const fadeEls = document.querySelectorAll("[data-fade-in]");
    fadeEls.forEach((el) => {
        el.style.animation = "riseIn 400ms ease forwards";
    });

    const staggerParents = document.querySelectorAll("[data-stagger]");
    staggerParents.forEach((parent) => {
        [...parent.children].forEach((child, index) => {
            child.style.animation = `riseIn 420ms ease ${index * 80}ms forwards`;
        });
    });
}

function wireTransactionFilter() {
    const filterInput = document.getElementById("transaction-filter");
    const table = document.getElementById("transactions-table");
    if (!filterInput || !table) {
        return;
    }

    filterInput.addEventListener("input", () => {
        const term = filterInput.value.trim().toLowerCase();
        const rows = table.querySelectorAll("tbody tr");

        rows.forEach((row) => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(term) ? "" : "none";
        });
    });
}

function setDescriptionMode(wrapper, showPaypal) {
    const bankDescription = wrapper.querySelector("[data-bank-description]");
    const paypalDescription = wrapper.querySelector("[data-paypal-description]");
    const toggleButton = wrapper.querySelector("[data-desc-toggle]");
    const badge = wrapper.querySelector("[data-desc-badge]");

    if (!paypalDescription || !toggleButton) {
        return;
    }

    wrapper.dataset.showPaypal = showPaypal ? "true" : "false";
    bankDescription.classList.toggle("hidden", showPaypal);
    paypalDescription.classList.toggle("hidden", !showPaypal);
    toggleButton.textContent = showPaypal ? "Show Bank" : "Show PayPal";

    if (badge) {
        badge.textContent = showPaypal ? "PayPal view" : "Bank view";
        badge.classList.toggle("desc-badge-view", showPaypal);
    }
}

function wireDescriptionToggles() {
    const wrappers = document.querySelectorAll("[data-description-wrapper][data-has-alt='true']");
    const toggleAllButton = document.getElementById("toggle-all-paypal-descriptions");

    wrappers.forEach((wrapper) => {
        const toggleButton = wrapper.querySelector("[data-desc-toggle]");
        if (!toggleButton) {
            return;
        }

        toggleButton.addEventListener("click", () => {
            const nextState = wrapper.dataset.showPaypal !== "true";
            setDescriptionMode(wrapper, nextState);
        });
    });

    if (!toggleAllButton) {
        return;
    }

    toggleAllButton.addEventListener("click", () => {
        const nextState = toggleAllButton.dataset.showPaypal !== "true";
        wrappers.forEach((wrapper) => {
            setDescriptionMode(wrapper, nextState);
        });

        toggleAllButton.dataset.showPaypal = nextState ? "true" : "false";
        toggleAllButton.textContent = nextState
            ? "Show all Bank descriptions"
            : "Show all PayPal descriptions";
    });
}

function autoDismissFlash() {
    const container = document.querySelector("[data-flash-container]");
    if (!container) {
        return;
    }

    setTimeout(() => {
        container.style.transition = "opacity 400ms ease";
        container.style.opacity = "0";
        setTimeout(() => {
            container.remove();
        }, 450);
    }, 2800);
}

document.addEventListener("DOMContentLoaded", () => {
    animateEntrance();
    wireTransactionFilter();
    wireDescriptionToggles();
    autoDismissFlash();
});
