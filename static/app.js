// Capitol Week - Client-side filtering, search, and interactivity

document.addEventListener("DOMContentLoaded", function () {
    const state = {
        chamber: "",
        type: "",
        committee: "",
        search: "",
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

    // Search input
    const searchInput = document.getElementById("search-filter");
    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                state.search = searchInput.value.toLowerCase().trim();
                applyFilters();
            }, 200);
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
            const matchSearch = !state.search ||
                (card.dataset.searchtext && card.dataset.searchtext.includes(state.search));

            if (matchChamber && matchType && matchCommittee && matchSearch) {
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

            const countEl = section.querySelector(".day-count");
            if (countEl) {
                const n = visibleCards.length;
                countEl.textContent = `${n} event${n !== 1 ? "s" : ""}`;
            }
        });

        // Update total count
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

        // Get week offset from the page
        const weekParam = new URLSearchParams(window.location.search).get("week");
        if (weekParam) params.set("week", weekParam);

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

    // Scroll to today on load (only for current week)
    const todaySection = document.querySelector(".day-section.today");
    if (todaySection) {
        setTimeout(() => {
            todaySection.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 300);
    }

    // Keyboard shortcut: Escape clears search
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && searchInput) {
            searchInput.value = "";
            state.search = "";
            applyFilters();
            searchInput.blur();
        }
        // "/" focuses search
        if (e.key === "/" && document.activeElement !== searchInput) {
            e.preventDefault();
            searchInput.focus();
        }
    });
});
