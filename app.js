(function () {
  const form = document.getElementById('sale-form');
  const items = document.getElementById('sale-items');
  const template = document.getElementById('line-template');
  const addButton = document.getElementById('add-line');
  if (!form || !items || !template || !addButton) return;

  const money = (value) => `${Number(value || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})} ج.م`;

  function recalculate() {
    let total = 0;
    let quantityTotal = 0;
    let selectedLines = 0;

    items.querySelectorAll('.sale-line').forEach((line) => {
      const select = line.querySelector('.product-select');
      const quantityInput = line.querySelector('.quantity-input');
      const option = select.options[select.selectedIndex];
      
      const price = option && option.dataset.price ? Number(option.dataset.price) : 0;
      const stock = option && option.dataset.stock !== undefined ? Number(option.dataset.stock) : Infinity;

      // قراءة الكمية
      let quantity = Number.parseInt(quantityInput.value || '0', 10);
      if (isNaN(quantity) || quantity < 0) quantity = 0;

      line.querySelector('.unit-price').textContent = money(price);

      if (select.value) {
        selectedLines += 1;
        total += price * quantity;
        quantityTotal += quantity;
        
        // تعيين الحد الأقصى فقط إذا كان المخزون المتاح أكبر من 0
        if (stock > 0) {
          quantityInput.max = String(stock);
          quantityInput.min = '1';
        } else {
          quantityInput.removeAttribute('max');
        }
      } else {
        quantityInput.removeAttribute('max');
      }
    });

    document.getElementById('line-count').textContent = String(selectedLines);
    document.getElementById('quantity-total').textContent = String(quantityTotal);
    document.getElementById('grand-total').textContent = money(total);
  }

  function bindLine(line) {
    line.querySelector('.product-select').addEventListener('change', (e) => {
      const quantityInput = line.querySelector('.quantity-input');
      // عند اختيار منتج، اجعل القيمة الافتراضية 1 إذا كانت 0
      if (e.target.value && (!quantityInput.value || quantityInput.value === '0')) {
        quantityInput.value = '1';
      }
      recalculate();
    });

    line.querySelector('.quantity-input').addEventListener('input', recalculate);
    
    line.querySelector('.remove-line').addEventListener('click', () => {
      const lines = items.querySelectorAll('.sale-line');
      if (lines.length === 1) {
        line.querySelector('.product-select').value = '';
        line.querySelector('.quantity-input').value = '1';
      } else {
        line.remove();
      }
      recalculate();
    });
  }

  items.querySelectorAll('.sale-line').forEach(bindLine);

  addButton.addEventListener('click', () => {
    const clone = template.content.cloneNode(true);
    items.appendChild(clone);
    bindLine(items.lastElementChild);
    recalculate();
  });

  form.addEventListener('submit', (event) => {
    const selected = [...items.querySelectorAll('.sale-line')].filter((line) => line.querySelector('.product-select').value);
    if (!selected.length) {
      event.preventDefault();
      alert('اختار منتجًا واحدًا على الأقل.');
      return;
    }
    for (const line of selected) {
      const select = line.querySelector('.product-select');
      const quantity = Number.parseInt(line.querySelector('.quantity-input').value || '0', 10);
      const stock = Number(select.options[select.selectedIndex].dataset.stock || '0');

      if (quantity < 1) {
        event.preventDefault();
        alert(`يجب أن تكون الكمية 1 على الأقل للمنتج: ${select.options[select.selectedIndex].text}`);
        return;
      }

      if (stock > 0 && quantity > stock) {
        event.preventDefault();
        alert(`راجع كمية المنتج: ${select.options[select.selectedIndex].text}. المتاح ${stock}.`);
        return;
      }
    }
  });

  recalculate();
})();