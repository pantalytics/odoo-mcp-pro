# Odoo App Selection Guide

A structured knowledge base for mapping business scenarios to the correct Odoo apps.
Used by the AI migration tool to avoid importing data into the wrong models.

## The Decision Framework

Two questions cut through most app selection confusion:
1. **Whose asset?** Yours (internal) or the customer's?
2. **Where is the work done?** Your location, their location, or remote?

## Quick Reference: The "Fix Things" Matrix

The most common source of confusion. Use this 2x2:

| | **Your equipment** | **Customer's equipment** |
|---|---|---|
| **Your location** | **Maintenance** | **Repairs** |
| **Customer's location** | (rare) | **Field Service** |
| **Remote** | — | **Helpdesk** |

Helpdesk acts as the front door: a ticket can escalate to Repairs (ship it to us), Field Service (we come to you), or a refund/return.

---

## App Profiles

### CRM

**Docs:** [odoo.com/documentation/19.0/.../crm](https://www.odoo.com/documentation/19.0/applications/sales/crm.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/sales/crm)

**When to use:**
- Sales pipeline with leads progressing through stages
- Lead scoring, prospecting, win/loss tracking
- Multiple salespeople nurturing opportunities

**When NOT to use:**
- You just need to send quotes and invoices (use Sales)
- You only need to store contacts (that's res.partner, not CRM)

**Key models:** `crm.lead`, `crm.team`, `utm.source`
**Typical company:** B2B with multi-stage sales cycles, companies with dedicated sales teams
**Related apps:** Sales (converts won opportunities into quotations), Marketing Automation

---

### Sales

**Docs:** [odoo.com/documentation/19.0/.../sales](https://www.odoo.com/documentation/19.0/applications/sales/sales.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/sales/sales)

**When to use:**
- Create quotations, confirm sales orders, invoice customers
- Pricelists, discounts, subscription billing
- Multi-channel selling (webshop, Amazon, POS)

**When NOT to use:**
- You only need pipeline/lead tracking (use CRM)
- You only purchase, never sell (use Purchase)

**Key models:** `sale.order`, `sale.order.line`, `sale.order.template`, `product.pricelist`
**Typical company:** Any company that sells products or services
**Related apps:** CRM (feeds opportunities), Inventory (fulfillment), Accounting (invoicing)

---

### Purchase

**Docs:** [odoo.com/documentation/19.0/.../purchase](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/purchase.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/purchase)

**When to use:**
- Send RFQs to vendors, manage purchase orders
- Vendor pricelists, blanket orders, call-for-tenders
- Automated reordering rules tied to inventory levels

**When NOT to use:**
- You only buy occasionally without formal PO workflow
- Internal requisitions only (use Approvals)

**Key models:** `purchase.order`, `purchase.order.line`, `product.supplierinfo`
**Typical company:** Manufacturers sourcing components, retailers replenishing stock
**Related apps:** Inventory (receiving), Accounting (vendor bills), Manufacturing (component sourcing)

---

### Inventory

**Docs:** [odoo.com/documentation/19.0/.../inventory](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/inventory)

**When to use:**
- Physical stock tracking (quantities, locations, multi-warehouse)
- Lot or serial number traceability (food, pharma, electronics, machines)
- Shipping, receiving, dropship flows

**When NOT to use:**
- You sell only services with no physical goods
- You only need a product catalog (product master lives in Sales too)

**Key models:** `stock.move`, `stock.picking`, `stock.lot`, `stock.quant`, `stock.warehouse`, `stock.location`, `product.template`, `product.product`
**Typical company:** Distributors, e-commerce, manufacturers, any company with a warehouse
**Related apps:** Manufacturing (consumes stock), Purchase (replenishes), Sales (triggers delivery)

---

### Manufacturing

**Docs:** [odoo.com/documentation/19.0/.../manufacturing](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/manufacturing.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/manufacturing)

**When to use:**
- Produce finished goods from raw materials using bills of materials
- Work orders, work centers, shop floor tracking
- Subcontract production to third parties

**When NOT to use:**
- You only assemble kits at point of sale (Inventory kits may suffice)
- You resell finished goods without transformation (use Inventory alone)

**Key models:** `mrp.production`, `mrp.bom`, `mrp.bom.line`, `mrp.workcenter`, `mrp.workorder`
**Typical company:** Factories, artisan producers, food processors
**Related apps:** Inventory (stock consumption), Purchase (components), Quality (inspection)

---

### Project

**Docs:** [odoo.com/documentation/19.0/.../project](https://www.odoo.com/documentation/19.0/applications/services/project.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/services/project)

**When to use:**
- Internal or client projects with tasks, stages, deadlines
- Timesheets for billing or internal time tracking
- Project profitability tracking

**When NOT to use:**
- Your "projects" are on-site service visits (use Field Service)
- You only need a simple to-do list (overkill)

**Key models:** `project.project`, `project.task`, `account.analytic.line` (timesheets)
**Typical company:** Consulting firms, agencies, software teams
**Related apps:** Timesheets, Sales (billable projects), Field Service (extends Project)

---

### Field Service

**Docs:** [odoo.com/documentation/19.0/.../field_service](https://www.odoo.com/documentation/19.0/applications/services/field_service.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/services/field_service)

**When to use:**
- Dispatch technicians to customer sites for installations, repairs, inspections
- Itinerary planning, on-site worksheets, mobile task management
- **Customer's equipment, at customer's location**

**When NOT to use:**
- Support is handled remotely (use Helpdesk)
- You maintain your own internal equipment (use Maintenance)
- Work is done at your facility on customer goods (use Repairs)

**Key models:** `project.task` (with `is_fsm=True`), worksheet templates, `product.product` (materials used)
**Typical company:** HVAC contractors, telecom installers, elevator service, machine manufacturers with after-sales service
**Related apps:** Helpdesk (ticket triggers field visit), Project (Field Service extends it), Sales (bill for work)

---

### Helpdesk

**Docs:** [odoo.com/documentation/19.0/.../helpdesk](https://www.odoo.com/documentation/19.0/applications/services/helpdesk.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/services/helpdesk)

**When to use:**
- Customer support tickets via email, chat, web forms
- SLAs, customer satisfaction ratings, ticket metrics
- After-sales hub: can trigger returns, repairs, refunds, field service tasks

**When NOT to use:**
- Primarily on-site technician work (use Field Service, triggered from Helpdesk)
- Internal IT ticketing only (works but is customer-facing by design)

**Key models:** `helpdesk.ticket`, `helpdesk.team`, `helpdesk.sla`
**Typical company:** SaaS vendors, equipment dealers with after-sales obligations
**Related apps:** Field Service (dispatch from ticket), Repairs (repair order from ticket), Sales (refunds/credit notes)

---

### Repairs

**Docs:** [odoo.com/documentation/19.0/.../repairs](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/repairs.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/repairs)

**When to use:**
- Customers send you broken products and you fix them at your facility
- Track parts used, warranty status, return logistics
- **Customer's product, your location**

**When NOT to use:**
- You go to the customer's site to fix things (use Field Service)
- You maintain your own machines (use Maintenance)

**Key models:** `repair.order`, `repair.line`, `stock.lot` (serial tracking)
**Typical company:** Electronics repair shops, equipment dealers with service centers
**Related apps:** Helpdesk (ticket triggers repair), Inventory (parts and returns), Sales (invoicing)

---

### Maintenance

**Docs:** [odoo.com/documentation/19.0/.../maintenance](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/maintenance.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/maintenance)

**When to use:**
- Schedule preventive maintenance on your own internal equipment
- Maintenance calendar with recurring requests
- **Your equipment, your location**

**When NOT to use:**
- You service customer equipment (use Field Service or Repairs)
- You need customer-facing ticketing (use Helpdesk)
- You track warranty/service dates on sold products (NOT this)

**Key models:** `maintenance.equipment`, `maintenance.request`, `maintenance.team`, `maintenance.equipment.category`
**Typical company:** Factories, facilities managers, fleet operators — anyone with capital equipment needing scheduled upkeep
**Related apps:** Manufacturing (equipment tied to work centers), Inventory (spare parts)

---

### Accounting

**Docs:** [odoo.com/documentation/19.0/.../accounting](https://www.odoo.com/documentation/19.0/applications/finance/accounting.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/finance/accounting)

**When to use:**
- Full general ledger, journal entries, financial statements
- Tax declarations, bank reconciliation, multi-currency
- Localized accounting (40+ country localizations)

**When NOT to use:**
- You only need basic invoicing (Invoicing app is lighter)
- You outsource all accounting to an external firm

**Key models:** `account.move`, `account.account`, `account.journal`, `account.tax`, `account.payment`
**Typical company:** Any company beyond the smallest
**Related apps:** Sales (invoices), Purchase (vendor bills), Inventory (stock valuation), Payroll

---

---

## Services & Operations

### Quality

**Docs:** [odoo.com/documentation/19.0/.../quality](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/quality.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/quality)

**When to use:**
- Quality checks on incoming/outgoing shipments or manufacturing
- Min/max tolerance measurements, quality alerts and corrective actions

**When NOT to use:**
- You only need visual inspection notes (use chatter/notes on picking)

**Key models:** `quality.check`, `quality.alert`, `quality.point`
**Typical company:** Manufacturers, food/pharma companies with compliance requirements
**Related apps:** Manufacturing (quality at work orders), Inventory (quality at receipts)

---

### Planning

**Docs:** [odoo.com/documentation/19.0/.../planning](https://www.odoo.com/documentation/19.0/applications/services/planning.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/services/planning)

**When to use:**
- Schedule employee shifts and work schedules
- Visual drag-and-drop resource planning, forecast workload capacity

**When NOT to use:**
- You plan project tasks, not shifts (use Project)
- You dispatch field technicians (use Field Service)

**Key models:** `planning.slot`, `planning.role`
**Typical company:** Retail, hospitality, manufacturing with shift workers
**Related apps:** HR (employees), Field Service, Manufacturing

---

### Rental

**Docs:** [odoo.com/documentation/19.0/.../rental](https://www.odoo.com/documentation/19.0/applications/sales/rental.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/sales/rental)

**When to use:**
- Rent out products with delivery and return tracking
- Manage rental periods, pricing, and availability

**When NOT to use:**
- You sell products outright (use Sales)
- You lease vehicles long-term (use Fleet)

**Key models:** `sale.order` (with rental fields), `sale.order.line`
**Typical company:** Equipment rental, car rental, tool hire companies
**Related apps:** Sales (extends it), Inventory (stock availability)

---

### Subscriptions

**Docs:** [odoo.com/documentation/19.0/.../subscriptions](https://www.odoo.com/documentation/19.0/applications/sales/subscriptions.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/sales/subscriptions)

**When to use:**
- Recurring invoices on a schedule (monthly, yearly)
- Subscription management with renewals, upselling, churn tracking

**When NOT to use:**
- One-time sales with no recurring component (use Sales)
- Rental of physical goods with delivery/return (use Rental)

**Key models:** `sale.order` (with recurrence), `sale.order.line`
**Typical company:** SaaS, media, gym memberships, service contracts
**Related apps:** Sales (extends it), Accounting (recurring invoices)

---

### PLM (Product Lifecycle Management)

**Docs:** [odoo.com/documentation/19.0/.../plm](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/plm.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/inventory_and_mrp/plm)

**When to use:**
- Engineering change orders on products and bills of materials
- Version control of BOMs, approval workflows for design changes

**When NOT to use:**
- You don't manufacture or have BOMs (use Sales/Inventory only)

**Key models:** `mrp.eco` (engineering change order), `mrp.bom`
**Typical company:** Manufacturers with engineering teams, product design companies
**Related apps:** Manufacturing (BOMs), Quality

---

## Sales & Marketing

### Appointments

**Docs:** [odoo.com/documentation/19.0/.../appointments](https://www.odoo.com/documentation/19.0/applications/sales/crm/track_leads/appointments.html)

**When to use:**
- Let clients book meetings in your agenda via a portal link
- Schedule consultations, demos, or onboarding sessions

**When NOT to use:**
- Internal meeting scheduling only (use Calendar)

**Key models:** `appointment.type`, `calendar.event`
**Typical company:** Consultants, sales teams, service providers
**Related apps:** Calendar, Website

---

### Email Marketing

**Docs:** [odoo.com/documentation/19.0/.../email_marketing](https://www.odoo.com/documentation/19.0/applications/marketing/email_marketing.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/email_marketing)

**When to use:**
- Design and send mass emails to leads, customers, or mailing lists
- Track open rates, clicks, and campaign performance

**When NOT to use:**
- Individual emails to specific customers (use Discuss/chatter)
- SMS campaigns (use SMS Marketing)

**Key models:** `mailing.mailing`, `mailing.list`, `mailing.contact`
**Typical company:** Any company doing bulk outreach or newsletters
**Related apps:** CRM (segments), Contacts, Marketing Automation

---

### SMS Marketing

**Docs:** [odoo.com/documentation/19.0/.../sms_marketing](https://www.odoo.com/documentation/19.0/applications/marketing/sms_marketing.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/sms_marketing)

**When to use:**
- Mass SMS campaigns to customers or leads
- SMS notifications and reminders

**When NOT to use:**
- Individual SMS to one customer (use chatter SMS)
- Email campaigns (use Email Marketing)

**Key models:** `mailing.mailing` (with SMS type), `sms.sms`
**Typical company:** Retail, hospitality, event companies
**Related apps:** Email Marketing, CRM

---

### Marketing Automation

**Docs:** [odoo.com/documentation/19.0/.../marketing_automation](https://www.odoo.com/documentation/19.0/applications/marketing/marketing_automation.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/marketing_automation)

**When to use:**
- Multi-step automated campaigns (email, SMS, actions) triggered by conditions
- Drip campaigns, lead nurturing sequences

**When NOT to use:**
- One-shot email blasts (use Email Marketing)
- Simple follow-up reminders (use Activities)

**Key models:** `marketing.campaign`, `marketing.activity`
**Typical company:** B2B with complex nurture flows, e-commerce
**Related apps:** Email Marketing, SMS Marketing, CRM

---

### Social Marketing

**Docs:** [odoo.com/documentation/19.0/.../social_marketing](https://www.odoo.com/documentation/19.0/applications/marketing/social_marketing.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/social_marketing)

**When to use:**
- Manage social media posts (Facebook, Twitter, LinkedIn) from Odoo
- Track engagement and visitor flows

**When NOT to use:**
- Internal communication (use Discuss)

**Key models:** `social.post`, `social.account`
**Typical company:** Marketing teams managing multiple social channels
**Related apps:** Website, Email Marketing

---

### Events

**Docs:** [odoo.com/documentation/19.0/.../events](https://www.odoo.com/documentation/19.0/applications/marketing/events.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/events)

**When to use:**
- Organize events, trainings, webinars with registration and ticketing
- Sell event tickets online, manage attendees

**When NOT to use:**
- Internal meetings (use Calendar)
- Online courses (use eLearning)

**Key models:** `event.event`, `event.registration`, `event.ticket`
**Typical company:** Conference organizers, training companies, associations
**Related apps:** Website (online registration), Sales (ticket sales)

---

## Website & eCommerce

### Website

**Docs:** [odoo.com/documentation/19.0/.../website](https://www.odoo.com/documentation/19.0/applications/websites/website.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/websites/website)

**When to use:**
- Build a company website with CMS, blog, forms
- Online portal for customers

**When NOT to use:**
- You only need an internal system (no public website needed)

**Key models:** `website`, `website.page`
**Typical company:** Any company wanting an online presence integrated with their ERP
**Related apps:** eCommerce, Events, eLearning, Online Jobs

---

### eCommerce

**Docs:** [odoo.com/documentation/19.0/.../ecommerce](https://www.odoo.com/documentation/19.0/applications/websites/ecommerce.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/websites/ecommerce)

**When to use:**
- Sell products online with shopping cart, checkout, payment
- B2C or B2B webshop integrated with inventory and accounting

**When NOT to use:**
- You only sell via phone/email (use Sales)
- Marketplace only (use Amazon/Lazada/Shopee connectors)

**Key models:** `sale.order` (from website), `payment.transaction`
**Typical company:** Retailers, manufacturers with direct sales
**Related apps:** Website (required), Inventory (fulfillment), Sales

---

### eLearning

**Docs:** [odoo.com/documentation/19.0/.../elearning](https://www.odoo.com/documentation/19.0/applications/websites/elearning.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/websites/elearning)

**When to use:**
- Publish online courses with video, quizzes, certifications
- Employee training or customer education

**When NOT to use:**
- In-person training events (use Events)
- Internal knowledge base (use Knowledge)

**Key models:** `slide.channel`, `slide.slide`
**Typical company:** Training companies, companies with employee onboarding
**Related apps:** Website, Surveys (assessments)

---

## HR & People

### Employees

**Docs:** [odoo.com/documentation/19.0/.../employees](https://www.odoo.com/documentation/19.0/applications/hr/employees.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/employees)

**When to use:**
- Centralize employee information (contacts, contracts, skills)
- Base module for all HR apps

**When NOT to use:**
- You only need to store customer contacts (use res.partner)

**Key models:** `hr.employee`, `hr.department`, `hr.job`
**Typical company:** Any company with employees
**Related apps:** All HR apps depend on this

---

### Recruitment

**Docs:** [odoo.com/documentation/19.0/.../recruitment](https://www.odoo.com/documentation/19.0/applications/hr/recruitment.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/recruitment)

**When to use:**
- Track job applications through a recruitment pipeline
- Publish job offers, manage candidates

**When NOT to use:**
- You track sales leads, not job candidates (use CRM)

**Key models:** `hr.applicant`, `hr.job`
**Typical company:** Growing companies with active hiring
**Related apps:** Employees, Website (Online Jobs)

---

### Time Off

**Docs:** [odoo.com/documentation/19.0/.../time_off](https://www.odoo.com/documentation/19.0/applications/hr/time_off.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/time_off)

**When to use:**
- Manage leave requests, allocations, and approval workflows
- Track vacation days, sick leave, remote work

**When NOT to use:**
- You track project time, not absence time (use Timesheets)

**Key models:** `hr.leave`, `hr.leave.allocation`, `hr.leave.type`
**Typical company:** Any company with leave policies
**Related apps:** Employees, Attendances, Planning

---

### Attendances

**Docs:** [odoo.com/documentation/19.0/.../attendances](https://www.odoo.com/documentation/19.0/applications/hr/attendances.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/attendances)

**When to use:**
- Track employee check-in/check-out times
- Monitor working hours, overtime

**When NOT to use:**
- You track time spent on tasks (use Timesheets)
- You manage shifts (use Planning)

**Key models:** `hr.attendance`
**Typical company:** Manufacturing, retail, any company requiring clock-in
**Related apps:** Employees, Payroll

---

### Expenses

**Docs:** [odoo.com/documentation/19.0/.../expenses](https://www.odoo.com/documentation/19.0/applications/hr/expenses.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/expenses)

**When to use:**
- Employees submit expense reports for reimbursement
- Validate, approve, and reinvoice expenses to customers

**When NOT to use:**
- Vendor bills for company purchases (use Purchase)

**Key models:** `hr.expense`, `hr.expense.sheet`
**Typical company:** Companies with traveling employees or reimbursable costs
**Related apps:** Accounting (reimbursement), Project (reinvoice to client)

---

### Appraisals

**Docs:** [odoo.com/documentation/19.0/.../appraisals](https://www.odoo.com/documentation/19.0/applications/hr/appraisals.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/appraisals)

**When to use:**
- Periodic employee performance reviews
- Goal setting and feedback loops

**When NOT to use:**
- You track skills and certifications (use Skills Management)

**Key models:** `hr.appraisal`, `hr.appraisal.goal`
**Typical company:** Companies with formal review cycles
**Related apps:** Employees, Skills Management

---

### Payroll

**Docs:** [odoo.com/documentation/19.0/.../payroll](https://www.odoo.com/documentation/19.0/applications/hr/payroll.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/payroll)

**When to use:**
- Calculate and process employee salaries
- Localized salary rules, tax computation, payslips

**When NOT to use:**
- You outsource payroll entirely (but can still use for structure)

**Key models:** `hr.payslip`, `hr.salary.rule`, `hr.contract`
**Typical company:** Companies processing payroll in-house
**Related apps:** Employees, Attendances, Time Off, Accounting

---

### Fleet

**Docs:** [odoo.com/documentation/19.0/.../fleet](https://www.odoo.com/documentation/19.0/applications/hr/fleet.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/fleet)

**When to use:**
- Manage company vehicles, leasing contracts, insurance
- Track fuel costs, maintenance, odometer readings

**When NOT to use:**
- You rent vehicles to customers (use Rental)
- You maintain factory equipment (use Maintenance)

**Key models:** `fleet.vehicle`, `fleet.vehicle.log.fuel`, `fleet.vehicle.log.services`
**Typical company:** Companies with company cars or delivery fleets
**Related apps:** HR (employee vehicle assignment), Accounting (cost tracking)

---

## Productivity & Communication

### Discuss

**Docs:** [odoo.com/documentation/19.0/.../discuss](https://www.odoo.com/documentation/19.0/applications/productivity/discuss.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/productivity/discuss)

**When to use:**
- Internal chat, channels, and the mail gateway (chatter on records)
- Always installed — it's the communication backbone

**Key models:** `mail.message`, `discuss.channel`, `mail.activity`
**Note:** This is a core module, always active. Not really a "selection" choice.

---

### Calendar

**Docs:** [odoo.com/documentation/19.0/.../calendar](https://www.odoo.com/documentation/19.0/applications/productivity/calendar.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/productivity/calendar)

**When to use:**
- Schedule internal meetings, sync with Google/Outlook calendars
- Reminders and invitations

**When NOT to use:**
- Client-facing appointment booking (use Appointments)

**Key models:** `calendar.event`, `calendar.attendee`
**Related apps:** CRM (meeting scheduling), Discuss

---

### Documents

**Docs:** [odoo.com/documentation/19.0/.../documents](https://www.odoo.com/documentation/19.0/applications/productivity/documents.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/productivity/documents)

**When to use:**
- Centralized document management with tags, workspaces, sharing
- Automated document workflows (e.g., scan → validate → archive)

**When NOT to use:**
- You just need file attachments on records (use built-in attachments)
- Wiki-style knowledge base (use Knowledge)

**Key models:** `documents.document`, `documents.tag`
**Typical company:** Companies with compliance or heavy document needs
**Related apps:** Accounting (auto-filing invoices), HR (employee documents)

---

### Knowledge

**Docs:** [odoo.com/documentation/19.0/.../knowledge](https://www.odoo.com/documentation/19.0/applications/productivity/knowledge.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/productivity/knowledge)

**When to use:**
- Internal wiki/knowledge base for procedures, policies, how-tos
- Collaborative editing, structured articles

**When NOT to use:**
- Customer-facing documentation (use Website or eLearning)
- File storage (use Documents)

**Key models:** `knowledge.article`
**Typical company:** Any company wanting structured internal documentation
**Related apps:** Documents, eLearning

---

### Sign

**Docs:** [odoo.com/documentation/19.0/.../sign](https://www.odoo.com/documentation/19.0/applications/productivity/sign.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/productivity/sign)

**When to use:**
- Send documents for electronic signature (contracts, NDAs, forms)
- Track signature status, multiple signers

**When NOT to use:**
- You need wet signatures or notarized documents

**Key models:** `sign.template`, `sign.request`
**Typical company:** Sales teams (contracts), HR (onboarding), Legal
**Related apps:** Sales (quote signing), HR (contracts)

---

### Surveys

**Docs:** [odoo.com/documentation/19.0/.../surveys](https://www.odoo.com/documentation/19.0/applications/marketing/surveys.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/marketing/surveys)

**When to use:**
- Create questionnaires, feedback forms, assessments
- Live survey sessions, scoring, certifications

**When NOT to use:**
- You need a full course platform (use eLearning)
- Simple internal forms (use custom models or Studio)

**Key models:** `survey.survey`, `survey.question`, `survey.user_input`
**Typical company:** HR (employee surveys), training, customer feedback
**Related apps:** eLearning, Recruitment (interview scoring)

---

### Approvals

**Docs:** [odoo.com/documentation/19.0/.../approvals](https://www.odoo.com/documentation/19.0/applications/hr/approvals.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/hr/approvals)

**When to use:**
- Formalized approval requests (budget, travel, purchases, etc.)
- Multi-level approval workflows

**When NOT to use:**
- Purchase order approval (built into Purchase app)
- Leave approval (built into Time Off app)

**Key models:** `approval.request`, `approval.category`
**Typical company:** Companies with formal approval processes
**Related apps:** Purchase, Expenses

---

## Infrastructure & Technical

### Studio

**Docs:** [odoo.com/documentation/19.0/.../studio](https://www.odoo.com/documentation/19.0/applications/studio.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/studio)

**When to use:**
- Customize Odoo UI, add fields, modify views without code
- Build simple custom apps

**When NOT to use:**
- Complex business logic (use custom Python modules)

**Note:** Enterprise only. Useful for adding custom fields that other apps lack.

---

### IoT (Internet of Things)

**Docs:** [odoo.com/documentation/19.0/.../iot](https://www.odoo.com/documentation/19.0/applications/general/iot.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/general/iot)

**When to use:**
- Connect physical devices (scales, printers, cameras) to Odoo
- Manufacturing shop floor integration

**When NOT to use:**
- You have no physical devices to connect

**Related apps:** Manufacturing, Point of Sale, Quality

---

### Point of Sale

**Docs:** [odoo.com/documentation/19.0/.../point_of_sale](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/sales/point_of_sale)

**When to use:**
- Retail checkout, restaurant orders, bar tabs
- Touch-screen interface for in-store sales

**When NOT to use:**
- Online sales only (use eCommerce)
- B2B quotation-based sales (use Sales)

**Key models:** `pos.order`, `pos.session`, `pos.config`
**Typical company:** Retail shops, restaurants, food trucks
**Related apps:** Inventory (stock), Accounting (payments), Restaurant (extension)

---

### Marketplace Connectors (Amazon, Lazada, Shopee)

**When to use:**
- Import orders from marketplaces into Odoo
- Sync deliveries and inventory with marketplace

**When NOT to use:**
- You only sell on your own webshop (use eCommerce)

**Related apps:** Sales, Inventory

---

### Frontdesk

**When to use:**
- Visitor check-in/check-out at reception
- Notify employees when their guest arrives

**Key models:** `frontdesk.visitor`
**Typical company:** Offices with reception desks, factories with visitor management

---

### Phone (VoIP)

**When to use:**
- Make and receive calls from within Odoo
- Click-to-call on contacts, call logging

**When NOT to use:**
- You don't use VoIP telephony

**Related apps:** CRM (call logging), Helpdesk

---

### WhatsApp

**When to use:**
- Send WhatsApp messages from records (invoices, tickets, orders)
- WhatsApp templates and automated messages

**When NOT to use:**
- Bulk marketing (limited by WhatsApp policies)

**Related apps:** Sales, Helpdesk, Discuss

---

### Live Chat

**Docs:** [odoo.com/documentation/19.0/.../livechat](https://www.odoo.com/documentation/19.0/applications/websites/livechat.html) | [GitHub](https://github.com/odoo/documentation/tree/19.0/content/applications/websites/livechat)

**When to use:**
- Real-time chat widget on your website for visitor support
- Chat routing to operators, canned responses

**When NOT to use:**
- Internal team chat (use Discuss)
- Ticket-based support (use Helpdesk)

**Key models:** `im_livechat.channel`
**Typical company:** eCommerce, SaaS with website support
**Related apps:** Website, Helpdesk

---

### Data Recycle

**When to use:**
- Automatically find and archive/delete old records
- GDPR compliance, data hygiene

**When NOT to use:**
- Active data cleanup (manual or scripted)

**Typical company:** Companies with data retention policies

---

### ESG

**When to use:**
- Track Environmental, Social, and Governance metrics
- ESG reporting and compliance

**Typical company:** Companies with sustainability reporting requirements
**Related apps:** Accounting

---

## Common Mistakes

| Mistake | Why it's wrong | Correct app |
|---------|---------------|-------------|
| Maintenance for sold products | Maintenance = YOUR equipment, not customer's | Field Service or Helpdesk |
| Field Service for remote support | Field Service = on-site visits | Helpdesk |
| CRM for contact management | CRM = sales pipeline, not an address book | res.partner (core) |
| Manufacturing for kit assembly | MRP is for production with work orders | Inventory (kits) |
| Project for field technician work | Project = office/desk work | Field Service |

---

## How to Use This Guide During Migration

Before importing data into any Odoo model:

1. Understand the client's business process (what do they do, for whom, where?)
2. Use the Decision Framework and Fix Things Matrix to identify the right app
3. Verify the required module is installed (`ir.module.module` via MCP)
4. Check the model's fields via MCP (`odoo://{model}/fields`) before mapping
5. If unsure — ask the client, don't guess

## Generating This Guide from Odoo

The app descriptions in this guide can be refreshed from any Odoo instance:

```python
# Via MCP: get all apps with descriptions
search_records(
    model="ir.module.module",
    domain=[["summary", "!=", ""], ["application", "=", true]],
    fields=["name", "shortdesc", "summary", "category_id", "state", "description"],
    order="shortdesc asc",
)
```

This gives you each app's official summary and description, plus whether it's installed.

## Sources

### Odoo Official Documentation (18.0/19.0)
- [Field Service](https://www.odoo.com/documentation/18.0/applications/services/field_service.html)
- [Helpdesk](https://www.odoo.com/documentation/18.0/applications/services/helpdesk.html)
- [After-Sales Services](https://www.odoo.com/documentation/18.0/applications/services/helpdesk/advanced/after_sales.html)
- [Maintenance](https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/maintenance.html)
- [Repairs](https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/repairs/repair_orders.html)
- [Manufacturing](https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/manufacturing.html)
- [Inventory](https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/inventory.html)
- [Sales](https://www.odoo.com/documentation/18.0/applications/sales/sales.html)
- [CRM](https://www.odoo.com/documentation/18.0/applications/sales/crm.html)
- [Purchase](https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/purchase.html)
- [Project](https://www.odoo.com/documentation/18.0/applications/services/project.html)

### Odoo Documentation Source Code
- [github.com/odoo/documentation](https://github.com/odoo/documentation) — full RST source of all Odoo docs. Use this for deep dives into specific features, configuration options, and workflows. Browse by version branch (e.g., `19.0`).
- Key paths in the repo:
  - `content/applications/inventory_and_mrp/` — Inventory, Manufacturing, Maintenance, Repairs, Quality, PLM
  - `content/applications/services/` — Field Service, Helpdesk, Project
  - `content/applications/sales/` — Sales, CRM, Subscriptions, Rental
  - `content/applications/finance/` — Accounting, Invoicing
  - `content/applications/hr/` — All HR apps
  - `content/applications/websites/` — Website, eCommerce, eLearning
  - `content/applications/productivity/` — Documents, Knowledge, Sign, Discuss

### Odoo Community & Learning
- [Odoo Forum: When to use Maintenance, Repairs, Field Services](https://www.odoo.com/forum/help-1/when-would-we-use-maintenance-repairs-and-field-services-apps-284702)
- [Odoo Academy / eLearning](https://www.odoo.com/slides)
- [Odoo App Store](https://www.odoo.com/app) — official app descriptions

### Odoo Instance (live data)
- `ir.module.module` — app descriptions, install status, categories
- `odoo://{model}/fields` — field definitions per model via MCP
