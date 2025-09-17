package taxhelper.chatservice.domain.law.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import taxhelper.chatservice.domain.law.entity.Law;

public interface LawRepository extends JpaRepository<Law, Long> {
}
