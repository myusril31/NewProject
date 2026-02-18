const watchlistData = [
  { pair: "BTC/USDT", price: 67245.1, change: 1.24 },
  { pair: "ETH/USDT", price: 3515.22, change: 0.83 },
  { pair: "SOL/USDT", price: 162.05, change: -0.48 },
  { pair: "XRP/USDT", price: 0.6123, change: 2.01 },
  { pair: "BNB/USDT", price: 591.19, change: -0.67 }
];

const askData = [67252.4, 67251.1, 67249.9, 67248.5, 67247.8, 67246.9];
const bidData = [67245.2, 67243.8, 67242.6, 67241.4, 67240.1, 67239.2];

function formatPrice(value) {
  return value.toLocaleString("en-US", { maximumFractionDigits: value > 1 ? 2 : 4 });
}

function formatChange(change) {
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(2)}%`;
}

const watchlist = document.getElementById("watchlist");
watchlistData.forEach((item) => {
  const li = document.createElement("li");
  const changeClass = item.change >= 0 ? "positive" : "negative";

  li.innerHTML = `
    <span>${item.pair}</span>
    <span>${formatPrice(item.price)}</span>
    <span class="${changeClass}">${formatChange(item.change)}</span>
  `;
  watchlist.appendChild(li);
});

function renderOrderList(id, values, className) {
  const node = document.getElementById(id);
  values.forEach((price) => {
    const qty = (Math.random() * 1.4 + 0.08).toFixed(3);
    const li = document.createElement("li");
    li.innerHTML = `<span class="${className}">${formatPrice(price)}</span><span>${qty} BTC</span>`;
    node.appendChild(li);
  });
}

renderOrderList("askList", askData, "negative");
renderOrderList("bidList", bidData, "positive");

setInterval(() => {
  const el = document.getElementById("lastPrice");
  const current = Number(el.textContent.replace(/,/g, ""));
  const next = current + (Math.random() - 0.5) * 12;
  el.textContent = formatPrice(next);
}, 2200);
