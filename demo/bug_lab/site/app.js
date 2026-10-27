"use strict";

const state = { products: [], cart: 0 };

function formatPrice(value) {
  return "$" + Number(value).toFixed(2);
}

function productCard(product) {
  const card = document.createElement("article");
  card.className = "card";

  const image = document.createElement("img");
  image.src = product.image;
  image.alt = product.name;

  const title = document.createElement("h3");
  title.textContent = product.name;

  const blurb = document.createElement("p");
  blurb.textContent = product.blurb;

  const price = document.createElement("div");
  price.className = "price";
  price.textContent = formatPrice(product.price);

  const button = document.createElement("button");
  button.className = "add";
  button.type = "button";
  button.textContent = "Add to cart";
  button.addEventListener("click", () => addToCart(product));

  card.append(image, title, blurb, price, button);
  return card;
}

function renderProducts(products) {
  const grid = document.getElementById("product-grid");
  grid.replaceChildren(...products.map(productCard));
  document.getElementById("status").textContent = products.length + " products";
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1500);
}

function addToCart(product) {
  state.cart += 1;
  document.getElementById("cart-count").textContent = String(state.cart);
  showToast(product.name + " added to cart");
}

async function loadProducts() {
  const response = await fetch("api/products");
  if (!response.ok) {
    throw new Error("Product API returned " + response.status);
  }
  const data = await response.json();
  state.products = data.products;
  renderProducts(state.products);
}

loadProducts().catch((error) => {
  document.getElementById("status").textContent = "Could not load products.";
  console.error(error);
});
