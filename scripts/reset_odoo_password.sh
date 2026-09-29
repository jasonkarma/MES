#!/bin/bash
# Script to reset Odoo admin password

echo "Resetting Odoo admin password to 'admin'..."

docker exec -it odoo-mes-lab-web-1 odoo shell -d mes_lab <<EOF
env['res.users'].browse(2).write({'password': 'admin'})
env.cr.commit()
exit()
EOF

echo "Password reset complete!"
echo "Use these credentials:"
echo "  Username: admin"
echo "  Password: admin"
