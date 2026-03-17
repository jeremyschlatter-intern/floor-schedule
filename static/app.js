// Capitol Week - Client-side filtering and interactivity

document.addEventListener("DOMContentLoaded", function () {
    const state = {
        chamber: "",
        type: "",
        committee: "",
    };

    // Filter buttons
    document.querySelectorAll(".btn-group").forEach(group => {
        const filterKey = group.dataset.filter;
        group.querySelectorAll(".filter-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                group.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                state[filterKey] = btn.dataset.value;
                applyFilters();
                updateIcalLink();
            });
        });
    });

    // Committee select
    const committeeSelect = document.getElementById("committee-filter");
    if (committeeSelect) {
        committeeSelect.addEventListener("change", () => {
            state.committee = committeeSelect.value;
            applyFilters();
            updateIcalLink();
        });
    }

    // Subscribe URL copy
    const copyBtn = document.getElementById("copy-subscribe");
    if (copyBtn) {
        copyBtn.addEventListener("click", () => {
            const url = buildIcalUrl(true);
            navigator.clipboard.writeText(url).then(() => {
                showToast("Subscription URL copied! Paste into your calendar app.");
            }).catch(() => {
                // Fallback
                prompt("Copy this subscription URL:", url);
            });
        });
    }

    function applyFilters() {
        const cards = document.querySelectorAll(".event-card");
        cards.forEach(card => {
            const matchChamber = !state.chamber || card.dataset.chamber === state.chamber;
            const matchType = !state.type || card.dataset.type === state.type;
            const matchCommittee = !state.committee ||
                (card.dataset.committee && card.dataset.committee.toLowerCase().includes(state.committee.toLowerCase()));

            if (matchChamber && matchType && matchCommittee) {
                card.classList.remove("filtered-out");
            } else {
                card.classList.add("filtered-out");
            }
        });

        // Update day sections
        document.querySelectorAll(".day-section").forEach(section => {
            const visibleCards = section.querySelectorAll(".event-card:not(.filtered-out)");
            const totalCards = section.querySelectorAll(".event-card");
            section.classList.toggle("filtered-empty", totalCards.length > 0 && visibleCards.length === 0);

            // Update day count
            const countEl = section.querySelector(".day-count");
            if (countEl) {
                const n = visibleCards.length;
                countEl.textContent = `${n} event${n !== 1 ? "s" : ""}`;
            }
        });

        // Update total count in header
        const totalVisible = document.querySelectorAll(".event-card:not(.filtered-out)").length;
        const countEl = document.querySelector(".event-count");
        if (countEl) {
            countEl.textContent = `${totalVisible} events`;
        }
    }

    function buildIcalUrl(absolute) {
        const params = new URLSearchParams();
        if (state.chamber) params.set("chamber", state.chamber);
        if (state.type) params.set("type", state.type);
        if (state.committee) params.set("committee", state.committee);

        const qs = params.toString();
        const path = "/calendar.ics" + (qs ? "?" + qs : "");
        if (absolute) {
            return window.location.origin + path;
        }
        return path;
    }

    function updateIcalLink() {
        const link = document.getElementById("ical-download");
        if (link) {
            link.href = buildIcalUrl(false);
        }
    }

    function showToast(msg) {
        const toast = document.getElementById("toast");
        if (!toast) return;
        toast.textContent = msg;
        toast.classList.remove("hidden");
        setTimeout(() => toast.classList.add("hidden"), 3000);
    }

    // Scroll to today on load
    const todaySection = document.querySelector(".day-section.today");
    if (todaySection) {
        setTimeout(() => {
            todaySection.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 300);
    }
});
