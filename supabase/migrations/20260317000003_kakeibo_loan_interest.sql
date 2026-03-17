-- ローン利息 手入力テーブル

CREATE TABLE IF NOT EXISTS public."Kakeibo_Loan_Interest" (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_id    text        NOT NULL,
    date       date        NOT NULL,
    amount     integer     NOT NULL CHECK (amount > 0),
    note       text        NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public."Kakeibo_Loan_Interest"        IS 'カードローン利息の手入力';
COMMENT ON COLUMN public."Kakeibo_Loan_Interest".loan_id IS 'Kakeibo_Loan_Accounts.loan_id に対応';
COMMENT ON COLUMN public."Kakeibo_Loan_Interest".amount  IS '利息金額（正値）';
