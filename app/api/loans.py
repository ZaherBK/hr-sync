from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.auth import api_require_permission
from app.models import (
    Loan, LoanSchedule, LoanRepayment,
    LoanInterestType, LoanTermUnit, LoanStatus, ScheduleStatus, RepaymentSource,
    Employee, LoanSettings
)
from app.schemas import LoanCreate, LoanOut, RepaymentCreate, RepaymentOut, LoanScheduleOut
from app.services.loan_calc import build_schedule, recompute_derived

router = APIRouter(prefix="/api/loans", tags=["loans"])

# Helper: DTI eligibility
async def _check_eligibility(db: AsyncSession, employee_id: int, amount_per_term: Decimal, unit: LoanTermUnit):
    # salary من Employee (موجود في موديلك)
    emp = await db.get(Employee, employee_id)
    if not emp or not emp.active:
        raise HTTPException(400, "Employee not eligible")

    salary = Decimal(emp.salary or 0)
    if salary <= 0:
        return  # لو مش مدخّل راتب، نسمح ونترك القرار للمدير

    settings = (await db.execute(select(LoanSettings).limit(1))).scalar_one_or_none()
    max_dti = Decimal(settings.max_dti) if settings else Decimal("0.30")

    # نحول القسط الشهري/الأسبوعي إلى نسبة من الراتب (تقريبية)
    # (لو راتبك شهري: قسط شهري مباشر؛ قسط أسبوعي × 4.333 للتقدير)
    if unit == LoanTermUnit.week:
        monthly_equivalent = amount_per_term * Decimal("4.333333333")
    else:
        monthly_equivalent = amount_per_term

    if salary > 0 and (monthly_equivalent / salary) > max_dti:
        raise HTTPException(400, "DTI exceeds company limit")

@router.get("/", response_model=list[LoanOut], dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def list_loans(status: LoanStatus | None = None, employee_id: int | None = None, db: AsyncSession = Depends(get_db)):
    q = select(Loan)
    if status:
        q = q.where(Loan.status == status)
    if employee_id:
        q = q.where(Loan.employee_id == employee_id)
    q = q.order_by(Loan.created_at.desc())
    res = await db.execute(q)
    return res.scalars().all()

@router.post("/", response_model=LoanOut, dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def create_loan(payload: LoanCreate, db: AsyncSession = Depends(get_db), user=Depends(api_require_permission("can_manage_loans"))):
    # منع أكثر من قرض نشط لو إعداد الشركة يطلب ذلك
    settings = (await db.execute(select(LoanSettings).limit(1))).scalar_one_or_none()
    if settings and settings.max_concurrent_loans == 1:
        exists = await db.execute(select(func.count()).select_from(Loan).where(
            Loan.employee_id == payload.employee_id,
            Loan.status.in_([LoanStatus.approved, LoanStatus.active])
        ))
        if exists.scalar_one() > 0:
            raise HTTPException(400, "Employee already has an active loan")

    loan = Loan(
        employee_id=payload.employee_id,
        principal=payload.principal,
        interest_type=LoanInterestType(payload.interest_type),
        annual_interest_rate=payload.annual_interest_rate,
        term_count=payload.term_count,
        term_unit=LoanTermUnit(payload.term_unit),
        start_date=payload.start_date,
        first_due_date=payload.first_due_date,
        fee=payload.fee,
        notes=payload.notes,
        status=LoanStatus.draft,
        created_by=user["id"]
    )
    db.add(loan)
    await db.flush()  # نحتاج id لتوليد الجدول

    rows = build_schedule(loan)
    
    # --- CORRECTION DTI (Désactivé comme demandé) ---
    if rows and False: # Mettre à True pour réactiver le check DTI
        # await _check_eligibility(db, loan.employee_id, rows[0].due_total, loan.term_unit)
        pass
    # --- FIN CORRECTION DTI ---

    for r in rows:
        db.add(r)

    # --- DEBUT DE LA CORRECTION ---
    # NE PAS FAIRE CECI - CELA CAUSE LE CRASH "MissingGreenlet"
    # loan.schedules = rows 
    
    # عند الموافقة مباشرةً (اختياري: تظل Draft لحين approve endpoint)
    loan.status = LoanStatus.approved
    
    # Passer 'rows' en argument pour éviter le lazy-load
    recompute_derived(loan, schedules=rows)

    # AJOUTER commit et refresh car get_db ne le fait plus
    await db.commit()
    await db.refresh(loan)
    # --- FIN DE LA CORRECTION ---
    
    return loan

@router.get("/{loan_id}", response_model=LoanOut, dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def get_loan(loan_id: int, db: AsyncSession = Depends(get_db)):
    loan = await db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    return loan

@router.get("/{loan_id}/schedule", response_model=list[LoanScheduleOut], dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def get_schedule(loan_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(LoanSchedule).where(LoanSchedule.loan_id == loan_id).order_by(LoanSchedule.sequence_no))
    return res.scalars().all()

@router.post("/{loan_id}/approve", response_model=LoanOut, dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def approve_loan(loan_id: int, db: AsyncSession = Depends(get_db)):
    loan = await db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.status != LoanStatus.approved and loan.status != LoanStatus.draft:
        raise HTTPException(400, "Loan already processed")

    loan.status = LoanStatus.active
    await db.commit()
    await db.refresh(loan)
    return loan

@router.post("/{loan_id}/repay", response_model=RepaymentOut, dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def repay(loan_id: int, payload: RepaymentCreate, db: AsyncSession = Depends(get_db), user=Depends(api_require_permission("can_manage_loans"))):
    loan = await db.get(Loan, loan_id)
    if not loan or loan.status not in [LoanStatus.active, LoanStatus.approved]:
        raise HTTPException(400, "Loan not active")

    # تخصيص الدفع لأقدم قسط غير مدفوع إذا لم يحدد schedule_id
    target = None
    if payload.schedule_id:
        target = await db.get(LoanSchedule, payload.schedule_id)
        if not target or target.loan_id != loan_id:
            raise HTTPException(400, "Invalid schedule")
    else:
        res = await db.execute(
            select(LoanSchedule).where(
                LoanSchedule.loan_id == loan_id,
                LoanSchedule.status.in_([ScheduleStatus.overdue, ScheduleStatus.partial, ScheduleStatus.pending])
            ).order_by(LoanSchedule.sequence_no)
        )
        target = res.scalars().first()

    if not target:
        raise HTTPException(400, "Nothing to pay")

    remaining = target.due_total - target.paid_total
    pay_amount = payload.amount if payload.amount <= remaining else remaining

    target.paid_total += pay_amount
    # تقسيم المبلغ على أصل/فائدة بنفس النسبة المتبقية
    if target.due_total > 0:
        # --- CORRECTION pour éviter la division par zéro si le paiement était déjà partiel ---
        remaining_due_before_pay = target.due_total - (target.paid_total - pay_amount)
        if remaining_due_before_pay > 0:
            p_ratio = (target.due_principal - target.paid_principal) / remaining_due_before_pay
            i_ratio = (target.due_interest - target.paid_interest) / remaining_due_before_pay
            target.paid_principal += (pay_amount * p_ratio)
            target.paid_interest  += (pay_amount * i_ratio)
        else:
            # Si le paiement restant était 0 (cas étrange), mettez tout sur le principal
            target.paid_principal += pay_amount
        # --- FIN CORRECTION ---

    if target.paid_total >= target.due_total:
        target.status = ScheduleStatus.paid
        target.paid_on = payload.paid_on
    else:
        target.status = ScheduleStatus.partial

    repayment = LoanRepayment(
        loan_id=loan_id, schedule_id=target.id, amount=pay_amount,
        source=payload.source, paid_on=payload.paid_on, notes=payload.notes, created_by=user["id"]
    )
    db.add(repayment)

    # تحديث المشتقات
    # ICI, nous devons charger les schedules car 'loan' ne les a pas
    # C'est OK de le faire car repay() est une fonction async
    await db.refresh(loan, ['schedules']) 
    recompute_derived(loan, schedules=loan.schedules) # Passez-les

    # إغلاق كامل؟
    if loan.outstanding_principal <= 0 and all(s.status == ScheduleStatus.paid for s in loan.schedules):
        loan.status = LoanStatus.paid

    await db.commit()
    await db.refresh(repayment)
    return repayment

@router.post("/{loan_id}/cancel", response_model=LoanOut, dependencies=[Depends(api_require_permission("can_manage_loans"))])
async def cancel_loan(loan_id: int, db: AsyncSession = Depends(get_db)):
    loan = await db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.repaid_total > 0:
        raise HTTPException(400, "Cannot cancel after repayments")
    loan.status = LoanStatus.canceled
    await db.commit()
    await db.refresh(loan)
    return loan
