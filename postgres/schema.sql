CREATE TABLE lab_identity (id uuid PRIMARY KEY);
INSERT INTO lab_identity VALUES (:'lab_id');
CREATE TABLE customers (
    id bigint PRIMARY KEY,
    email text NOT NULL UNIQUE
);
CREATE TABLE products (
    id bigint PRIMARY KEY,
    name text NOT NULL UNIQUE,
    price_cents integer NOT NULL CHECK (price_cents > 0)
);
CREATE TABLE batches (
    id bigint PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE orders (
    id bigint PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers,
    batch_id bigint NOT NULL REFERENCES batches,
    status text NOT NULL CHECK (status IN ('paid', 'pending')),
    total_cents bigint NOT NULL CHECK (total_cents > 0)
);
CREATE TABLE order_items (
    order_id bigint NOT NULL REFERENCES orders ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price_cents integer NOT NULL CHECK (unit_price_cents > 0),
    PRIMARY KEY (order_id, product_id)
);
INSERT INTO customers SELECT n, 'customer-' || n || '@example.invalid' FROM generate_series(1, 20) n;
INSERT INTO products SELECT n, 'product-' || n, 100 + n * 25 FROM generate_series(1, 10) n;
