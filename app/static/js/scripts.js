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

function wireReviewCategoryInterlock() {
    const sections = document.querySelectorAll("[data-category-flag-map]");
    sections.forEach((section) => {
        let map = {};
        try {
            map = JSON.parse(section.dataset.categoryFlagMap || "{}");
        } catch (_error) {
            map = {};
        }

        const forms = section.querySelectorAll("form");
        forms.forEach((form) => {
            const categorySelect = form.querySelector("select[name='category_name']");
            const categoryCustom = form.querySelector("input[name='category_name_custom']");
            const flagSelect = form.querySelector("select[name='household_flag']");
            if (!categorySelect || !flagSelect) {
                return;
            }

            categorySelect.addEventListener("change", () => {
                const selected = categorySelect.value;
                if (selected === "__new__" && categoryCustom) {
                    categoryCustom.focus();
                    return;
                }

                if (selected && map[selected]) {
                    flagSelect.value = map[selected];
                }
            });
        });
    });
}

function wireCsrfProtection() {
    const token = document.querySelector("meta[name='csrf-token']")?.content;
    if (!token) {
        return;
    }
    document.querySelectorAll("form[method='post'], form[method='POST']").forEach((form) => {
        if (form.querySelector("input[name='csrf_token']")) {
            return;
        }
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "csrf_token";
        input.value = token;
        form.appendChild(input);
    });
}

function wireDailyTimeline() {
    const form = document.querySelector("[data-timeline-form]");
    const slider = form?.querySelector("[data-timeline-slider]");
    const picker = form?.querySelector("[data-timeline-date]");
    if (!form || !slider || !picker) return;
    const minimum = new Date(`${form.dataset.minDate}T00:00:00`);
    const day = 86400000;
    const syncSlider = () => {
        const selected = new Date(`${picker.value}T00:00:00`);
        slider.value = Math.max(0, Math.min(365, Math.round((selected - minimum) / day)));
    };
    slider.addEventListener("input", () => {
        const selected = new Date(minimum.getTime() + Number(slider.value) * day);
        picker.value = selected.toISOString().slice(0, 10);
    });
    picker.addEventListener("change", syncSlider);
    syncSlider();
}

document.addEventListener("DOMContentLoaded", () => {
    wireCsrfProtection();
    animateEntrance();
    wireTransactionFilter();
    wireDescriptionToggles();
    wireReviewCategoryInterlock();
    autoDismissFlash();
    wireDailyTimeline();
});
